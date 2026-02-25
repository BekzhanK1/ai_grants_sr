"""
Access request — orchestrates OpenAI chat completion with multi-turn tool calling.

Flow:
  1. Send user request + tools to OpenAI.
  2. Model returns tool_calls (typically get_user_current_permissions first).
  3. Execute tool calls, feed results back as tool-role messages.
  4. Model sees current permissions, decides what to assign (or says "already exists").
  5. Execute remaining action tool calls.
  6. Return final answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.core.config import settings
from app.core.exceptions import AIServiceError
from app.data.reference import build_permissions_tree_context
from app.services import db_service
from app.services.llm import create_chat_completion

from .tool_definitions import build_tools
from .tool_executor import execute_tool_call, execute_tool_call_preview

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5  # safety: max back-and-forth cycles with the model

SYSTEM_PROMPT = """\
Ты — безопасный AI-администратор прав доступа в системе Smart Remont.

КОНТЕКСТ:
- employee_id всегда передаётся в запросе (он берётся из аутентификации).
- Не нужно искать сотрудника — он уже известен.
- Пользователь ОБЯЗАН указать причину (reason), зачем ему нужны права.

ПРОВЕРКА ПРИЧИНЫ (reason):
Перед выполнением ЛЮБЫХ действий ты ОБЯЗАН проверить поле «reason»:
1. Причина не должна быть бессмысленной (набор символов, «asdf», «123», «test», одно слово, повтор одного символа).
2. Причина должна быть логически связана с запрашиваемыми правами.
3. Причина должна объяснять бизнес-необходимость.
ВАЖНО: Не будь слишком бюрократичным. Если причина "Работа с документами" для доступа к договорам — это ОК. Если причина "Трудоустройство" для выдачи роли — это ОК. Отказывай только в явных случаях несоответствия или спама.

ДЕЙСТВУЙ, А НЕ БОЛТАЙ:
   - Если ты нашел подходящие права — ВЫЗЫВАЙ инструменты `add_grant`, `add_interface_button` и т.д.
   - НЕ пиши "Рекомендую выдать...", просто ВЫДАЙ.
   - Отвечай текстом только после того, как вызвал инструменты.
   
ПОРЯДОК ДЕЙСТВИЙ:
1. Проверь адекватность причины (reason).
2. ПОИСК ПРАВ:
   - Пользователь часто не знает точных названий. Он описывает задачу своими словами.
   - Извлеки КЛЮЧЕВЫЕ СЛОВА из запроса (существительные, глаголы действия).
   - Если просят "работать с договорами" — ищи "договор" в menus и grants.
   - Если просят "ставить прорабов" — ищи "прораб" или "назначение" в grants.
   - Если поиск на русском не даёт результатов — попробуй английские аналоги или транслитерацию.
     Пример: "колл-центр" → ищи "call center", "call-centra", "call".
     Пример: "email" → "почта" и наоборот.
   - Разбивай сложные слова: "шоурум" → ищи также "шоу-рум", "шоу", "рум".
   - Подбирай синонимы: "логистика" → "склад", "остатки" (menu 388); "долг" → "корректировка", "финансы" (menu 902).
   - "Revit" → ищи "ревит" (grant 1250).
   - "Диспетчер по заявкам" → ищи роль 110.
   - "KPI" → ищи меню 528.
   - Ищи через `search_menu`, `search_group`, `search_grant`. Лучше сделать несколько поисков разными словами, чем сказать "я не знаю".
3. Выбор из результатов поиска:
   - ВНИМАНИЕ: Если пользователь просит НЕСКОЛЬКО вещей (например, "прорабов И бригады"), ты ОБЯЗАН найти и выдать права ДЛЯ КАЖДОГО пункта.
   - Не останавливайся на первом найденном совпадении. Проверь, полностью ли удовлетворен запрос.
4. Выбор из результатов поиска:
   - Если найдена ровно одна запись — используй её.
   - Если найдено несколько, но одна из них ТОЧНО совпадает по названию — выбери её.
   - Если вариантов много — выбери наиболее подходящие по смыслу (1-3 шт) и предложи пользователю или, если уверенность высока, назначь их.
   - ВАЖНО: Доступ обычно состоит из МЕНЮ (интерфейс) и ПРАВА (действие).
     Если даешь право "Редактировать долг", найди и дай меню "Ручная корректировка долга" (902).
     Если даешь право "Остатки" (489), найди меню "Остатки на складе" (388).
     Без меню пользователь не сможет воспользоваться правом.
5. Перед ЛЮБЫМ назначением прав вызови `get_user_current_permissions`.
6. Посмотри результат: если нужный ID УЖЕ есть — НЕ вызывай функцию назначения.
7. Если пользователь скинул URL вместо menu_id — вызови `get_menu_by_url`.

СПЕЦИФИЧЕСКИЕ ПРАВИЛА:
- "Сброс пароля": уточни, для "партнера" (grant 10222) или "сотрудника" (grant 607). Если в запросе есть слово "партнер", выбирай 10222.

КРИТИЧНЫЕ ПРАВИЛА БЕЗОПАСНОСТИ:
- `assign_role` и `link_module` — это Toggle-переключатели. Повторный вызов УДАЛИТ право! Поэтому проверка через `get_user_current_permissions` обязательна.
- НИКОГДА не назначай группу «Администраторы» (group_id=1).
- НИКОГДА не открывай меню администрирования (menu_id=1, 2, 4, 10).
- Если просят админские права — откажи.
- Если не уверен в ID — переспроси у пользователя, но сначала ПОПРОБУЙ найти сам.

ФОРМАТ ОТВЕТА:
- Отвечай на русском языке.
- После выполнения действий кратко перечисли, что было сделано.
- Если у сотрудника уже есть запрошенное право — скажи об этом.
"""


async def process_user_request(
    *,
    user_id: int,
    prompt: str,
    reason: str,
) -> dict[str, Any]:
    """
    End-to-end pipeline with multi-turn tool calling.

    The model may do several rounds:
      round 0: validate reason adequacy (LLM refuses if bad reason)
      round 1: search_menu / search_group / search_grant → finds IDs
      round 2: get_user_current_permissions → sees current state
      round 3: assign_role / add_grant / ... → performs actions
      (optionally more rounds if model chains calls)
    """
    tools = build_tools()
    all_results: list[dict[str, Any]] = []

    logger.info(
        "Processing request  user_id=%s  prompt=%r  reason=%r",
        user_id,
        prompt[:120],
        reason[:120],
    )

    # 0. Check Daily Limit
    if settings.DAILY_LIMIT_ON:
        limit_check = await db_service.check_daily_limit(user_id)
        if not limit_check.get("is_allowed", False):
            error_msg = limit_check.get("error_message") or "Daily limit exceeded"
            logger.warning("User %s blocked by daily limit: %s", user_id, error_msg)
            raise AIServiceError(error_msg)

    employee_context = await db_service.get_employee_context(user_id)
    permissions_tree = await build_permissions_tree_context()
    logger.info("Employee context loaded for user_id=%s: %s", user_id, employee_context)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"employee_id: {user_id}\n"
                f"employee_context: {employee_context}\n"
                f"permissions_reference_tree:\n{permissions_tree}\n"
                f"запрос: {prompt}\n"
                f"причина: {reason}"
            ),
        },
    ]

    completion_id = ""

    for _round in range(MAX_TOOL_ROUNDS):
        # ── Call OpenAI (shared LLM module) ───────────────────────────
        try:
            completion = await create_chat_completion(
                messages,
                tools=tools,
                tool_choice="auto",
            )
        except Exception as exc:
            raise AIServiceError(f"OpenAI API error: {exc}") from exc

        completion_id = completion.id
        message = completion.choices[0].message
        tool_calls = message.tool_calls or []

        logger.info(
            "Round %d: model returned %d tool call(s), finish_reason=%s",
            _round + 1,
            len(tool_calls),
            completion.choices[0].finish_reason,
        )

        # If no tool calls, model is done — it produced a text answer
        if not tool_calls:
            break

        # ── Execute tool calls ───────────────────────────────────────
        results = list(
            await asyncio.gather(
                *(execute_tool_call(tc, user_id=user_id) for tc in tool_calls)
            )
        )
        all_results.extend(results)

        # ── Collect results for the next round ───────────────────────
        # Append assistant message with tool_calls
        messages.append(message.model_dump())

        for res in results:
            # Content that goes back to the model
            if "data" in res:
                content = json.dumps(res["data"], ensure_ascii=False)
            elif res["status"] == "ok":
                content = json.dumps({"status": "ok"}, ensure_ascii=False)
            elif res["status"] == "blocked":
                content = json.dumps(
                    {"status": "blocked", "error": res.get("error", "")},
                    ensure_ascii=False,
                )
            else:
                content = json.dumps(
                    {"status": "error", "error": res.get("error", "")},
                    ensure_ascii=False,
                )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": res["tool_call_id"],
                    "content": content,
                }
            )

    else:
        logger.warning("Reached MAX_TOOL_ROUNDS=%d, forcing stop", MAX_TOOL_ROUNDS)

    # ── Audit logging ────────────────────────────────────────────────
    # Filter "write" actions (exclude search_* and get_*)
    _READ_ONLY_TOOLS = {
        "search_menu",
        "search_group",
        "search_grant",
        "get_user_current_permissions",
        "get_menu_by_url",
    }

    significant_actions = [
        {
            "tool": r["tool"],
            "args": r["args"],
            "status": r.get("status"),
            "error": r.get("error"),
            "sql": r.get("sql"),
            "entity_name": r.get("entity_name"),
            "entity_id": r.get("entity_id"),
        }
        for r in all_results
        if r["tool"] not in _READ_ONLY_TOOLS
    ]

    status = "success"
    if any(r.get("status") == "error" for r in all_results):
        status = "error"
    elif any(r.get("status") == "blocked" for r in all_results):
        status = "blocked"

    asyncio.create_task(
        db_service.log_ai_request(
            user_id=user_id,
            prompt=prompt,
            reason=reason,
            ai_decision=significant_actions,
            ai_message=message.content,
            execution_status=status,
        )
    )

    return {
        "id": completion_id,
        "tool_calls": [r for r in all_results if r["tool"] not in _READ_ONLY_TOOLS],
        "ai_message": message.content,
    }


async def preview_user_request(
    *,
    user_id: int,
    prompt: str,
    reason: str,
    module_id: int | None = None,
) -> dict[str, Any]:
    """
    Preview-only вариант access request:
    - Запускает тот же LLM с tools, но action-инструменты НЕ пишут в БД.
    - Возвращает список планируемых действий (tool_calls) + SQL для каждого.
    - Используется для шага \"preview\" на фронте, перед фактическим исполнением.
    """
    tools = build_tools()
    all_results: list[dict[str, Any]] = []

    logger.info(
        "Previewing access request  user_id=%s  module_id=%s  prompt=%r  reason=%r",
        user_id,
        module_id,
        prompt[:120],
        reason[:120],
    )

    employee_context = await db_service.get_employee_context(user_id)
    permissions_tree = await build_permissions_tree_context()

    # Добавляем модуль в контекст, чтобы LLM фокусировался на нужном модуле
    module_context_line = ""
    if module_id is not None:
        module_context_line = f"module_id_context: {module_id}\n"

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"employee_id: {user_id}\n"
                f"employee_context: {employee_context}\n"
                f"permissions_reference_tree:\n{permissions_tree}\n"
                f"{module_context_line}"
                f"запрос: {prompt}\n"
                f"причина: {reason}"
            ),
        },
    ]

    completion_id = ""

    for _round in range(MAX_TOOL_ROUNDS):
        # ── Call OpenAI (shared LLM module) ───────────────────────────
        try:
            completion = await create_chat_completion(
                messages,
                tools=tools,
                tool_choice="auto",
            )
        except Exception as exc:
            raise AIServiceError(f"OpenAI API error (preview): {exc}") from exc

        completion_id = completion.id
        message = completion.choices[0].message
        tool_calls = message.tool_calls or []

        logger.info(
            "[PREVIEW] Round %d: model returned %d tool call(s), finish_reason=%s",
            _round + 1,
            len(tool_calls),
            completion.choices[0].finish_reason,
        )

        # If no tool calls, model is done — it produced a text answer
        if not tool_calls:
            break

        # ── Execute tool calls in PREVIEW mode ────────────────────────
        results = list(
            await asyncio.gather(
                *(execute_tool_call_preview(tc, user_id=user_id) for tc in tool_calls)
            )
        )
        all_results.extend(results)

        # ── Collect results for the next round ───────────────────────
        messages.append(message.model_dump())

        for res in results:
            if "data" in res:
                content = json.dumps(res["data"], ensure_ascii=False)
            elif res.get("sql"):
                # Для action-инструментов достаточно вернуть sql+args
                content = json.dumps(
                    {
                        "status": res.get("status"),
                        "sql": res.get("sql"),
                        "entity_id": res.get("entity_id"),
                        "entity_name": res.get("entity_name"),
                    },
                    ensure_ascii=False,
                )
            else:
                content = json.dumps(
                    {"status": res.get("status"), "error": res.get("error", "")},
                    ensure_ascii=False,
                )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": res["tool_call_id"],
                    "content": content,
                }
            )
    else:
        logger.warning("[PREVIEW] Reached MAX_TOOL_ROUNDS=%d, forcing stop", MAX_TOOL_ROUNDS)

    # Для превью аудита не пишем (ничего не применили)
    return {
        "id": completion_id,
        "tool_calls": all_results,
        "ai_message": message.content if message and message.content else None,
    }


VERDICT_PROMPT = """\
Ты — проверяющий заявки на доступ в Smart Remont.

Сотрудник вручную выбрал пункты меню и права и указал обоснование.
Твоя задача: по досье сотрудника и обоснованию дать вердикт — одобрить или отклонить заявку.

Ответь строго в формате:
ВЕРДИКТ: ОДОБРЕНО
или
ВЕРДИКТ: ОТКЛОНЕНО

После строки с вердиктом напиши краткое объяснение (1–3 предложения) на русском.
Одобряй, если обоснование связано с должностью/задачами. Отклоняй при явном несоответствии или пустом обосновании.
"""


async def preview_user_request_from_selection(
    *,
    user_id: int,
    module_id: int | None,
    menu_ids: list[int],
    grant_ids: list[int],
    reason: str,
) -> dict[str, Any]:
    """
    Превью заявки по ручному выбору: ИИ даёт вердикт по обоснованию и досье,
    без вызова инструментов. При одобрении возвращаем tool_calls для выбранных меню/прав.
    """
    if not menu_ids and not grant_ids:
        return {
            "id": "",
            "tool_calls": [],
            "ai_message": "Выберите хотя бы один пункт меню или право.",
        }

    employee_context = await db_service.get_employee_context(user_id)

    menu_names: list[str] = []
    for mid in menu_ids:
        name = await db_service.get_menu_display_name(mid)
        menu_names.append(name)
    grant_names: list[str] = []
    for gid in grant_ids:
        name = await db_service.get_grant_display_name(gid)
        grant_names.append(name)

    selection_text = []
    if menu_names:
        selection_text.append("Меню: " + ", ".join(menu_names))
    if grant_names:
        selection_text.append("Права: " + ", ".join(grant_names))

    user_content = (
        f"Сотрудник запросил выдачу:\n"
        f"{chr(10).join(selection_text)}\n\n"
        f"Обоснование: {reason}\n\n"
        f"Досье сотрудника:\n{employee_context}"
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": VERDICT_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        completion = await create_chat_completion(messages, tools=None)
    except Exception as exc:
        raise AIServiceError(f"OpenAI API error (verdict): {exc}") from exc

    completion_id = getattr(completion, "id", "") or ""
    message = completion.choices[0].message
    content = (message.content or "").strip().upper()

    approved = "ОТКЛОНЕНО" not in content and "ОТКЛОНЕН" not in content
    if "ВЕРДИКТ:" in content:
        approved = "ОДОБРЕНО" in content.split("ВЕРДИКТ:")[-1].split("\n")[0]

    tool_calls: list[dict[str, Any]] = []
    if approved:
        for mid in menu_ids:
            name = await db_service.get_menu_display_name(mid)
            tool_calls.append({
                "tool_call_id": f"sel-menu-{mid}",
                "tool": "add_interface_button",
                "args": {"employee_id": user_id, "menu_id": mid},
                "status": "pending",
                "entity_id": mid,
                "entity_name": name,
                "sql": f"SELECT admin.employee_menu__add(employee_id_ := {user_id}, menu_id_ := {mid})",
            })
        for gid in grant_ids:
            name = await db_service.get_grant_display_name(gid)
            tool_calls.append({
                "tool_call_id": f"sel-grant-{gid}",
                "tool": "add_grant",
                "args": {"employee_id": user_id, "grant_id": gid},
                "status": "pending",
                "entity_id": gid,
                "entity_name": name,
                "sql": (
                    f"INSERT INTO admin.employee_grant_tab (employee_id, grant_id) "
                    f"VALUES ({user_id}, {gid}) ON CONFLICT DO NOTHING"
                ),
            })

    return {
        "id": completion_id,
        "tool_calls": tool_calls,
        "ai_message": message.content if message and message.content else None,
    }


async def execute_user_actions(
    *,
    user_id: int,
    actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Исполнение заранее просмотренных действий без повторного вызова LLM.

    На вход:
      - user_id: ID сотрудника
      - actions: список dict {tool, args}
    Для каждого элемента вызывается execute_tool_call с теми же tool/args,
    что использовались на шаге превью.
    """
    from types import SimpleNamespace

    results: list[dict[str, Any]] = []
    for idx, action in enumerate(actions):
        name = action.get("tool")
        args = action.get("args") or {}
        if not name:
            continue

        dummy_call = SimpleNamespace(
            id=f"manual-{idx}",
            function=SimpleNamespace(
                name=name,
                arguments=json.dumps(args, ensure_ascii=False),
            ),
        )
        logger.info(
            "[EXECUTE_USER_ACTIONS] Executing tool=%s for user_id=%s with args=%s",
            name,
            user_id,
            args,
        )
        res = await execute_tool_call(dummy_call, user_id=user_id)
        logger.info(
            "[EXECUTE_USER_ACTIONS] Result for tool=%s user_id=%s: status=%s sql=%s",
            name,
            user_id,
            res.get("status"),
            res.get("sql"),
        )
        results.append(res)

    return results
