"""
AI service — orchestrates OpenAI chat completion with multi-turn tool calling.

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

from openai import AsyncOpenAI

from app.core.config import settings
from app.core.exceptions import AIServiceError
from app.services import db_service
from app.services.tool_definitions import build_tools
from app.services.tool_executor import execute_tool_call

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

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
    logger.info("Employee context loaded for user_id=%s: %s", user_id, employee_context)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"employee_id: {user_id}\n"
                f"employee_context: {employee_context}\n"
                f"запрос: {prompt}\n"
                f"причина: {reason}"
            ),
        },
    ]

    completion_id = ""

    for _round in range(MAX_TOOL_ROUNDS):
        # ── Call OpenAI ──────────────────────────────────────────────
        try:
            completion = await _client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                temperature=0.0,
                max_tokens=1024,
                tools=tools,
                tool_choice="auto",
                messages=messages,
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

    # We only want to log significant actions, but the user requested:
    # "ai_decision jsonb -- список выполненных действий (инструмент + аргументы)."
    # So we should probably log EVERYTHING strictly speaking, or just the write actions.
    # Usually audit logs care about changes. Let's log effective actions.

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

    # Determine execution status
    # 'error' if any action failed
    # 'blocked' if any action was blocked
    # 'success' otherwise (even if no actions were taken, e.g. "already exists")

    status = "success"
    if any(r.get("status") == "error" for r in all_results):
        status = "error"
    elif any(r.get("status") == "blocked" for r in all_results):
        status = "blocked"
    # If no significant actions were taken, it might be a refusal or just Info.
    # But let's stick to the requested statuses.

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
