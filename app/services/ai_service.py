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
3. Причина должна объяснять бизнес-необходимость: зачем сотруднику этот доступ, для какой задачи.
Если причина неадекватная, бессмысленная или не связана с запросом — ОТКАЖИ в выполнении и попроси указать корректную причину. НЕ вызывай никаких инструментов.

ПОРЯДОК ДЕЙСТВИЙ:
1. Проверь адекватность причины (reason). Если не прошла — откажи сразу.
2. Если пользователь указал название (а не числовой ID) — найди ID через search_menu / search_group / search_grant. Поиск нечёткий: находятся варианты с разным падежом или написанием (например «менеджера call-центра» → «Менеджер call-centra»). Если запрос не дал результатов — попробуй другой вариант написания (например без падежа или латиницей).
3. Выбор из результатов поиска:
   - Если найдена ровно одна запись — используй её.
   - Если найдено несколько, но одна из них ТОЧНО совпадает по названию с запросом пользователя (например, пользователь написал «Подрядчики» и есть запись с menu_name ровно «Подрядчики») — выбери её и продолжай. В ответе укажи, какой именно элемент выбран.
   - Если точных совпадений нет или их больше одного — перечисли варианты и попроси пользователя уточнить.
4. Перед ЛЮБЫМ назначением прав вызови get_user_current_permissions, чтобы узнать текущие права.
5. Посмотри результат: если нужный ID УЖЕ есть в списке — НЕ вызывай функцию назначения. Сообщи: «Этот доступ уже открыт».
6. Если пользователь скинул URL вместо menu_id — вызови get_menu_by_url.

КРИТИЧНЫЕ ПРАВИЛА БЕЗОПАСНОСТИ:
- assign_role и link_module — это Toggle-переключатели. Повторный вызов УДАЛИТ право! Поэтому проверка через get_user_current_permissions обязательна.
- НИКОГДА не назначай группу «Администраторы» (group_id=1).
- НИКОГДА не открывай меню администрирования (menu_id=1, 2, 4, 10).
- Если просят админские права — откажи и объясни, что это запрещено политикой безопасности.
- Если не уверен в ID — переспроси у пользователя, а не угадывай.

ФОРМАТ ОТВЕТА:
- Отвечай на русском языке.
- После выполнения действий кратко перечисли, что было сделано, и укажи причину, на основании которой доступ был выдан.
- Если у сотрудника уже есть запрошенное право — скажи об этом.
- Если причина не прошла проверку — объясни, что именно не так, и попроси переформулировать.
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
        user_id, prompt[:120], reason[:120],
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"employee_id: {user_id}\n"
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
        results = list(await asyncio.gather(*(execute_tool_call(tc) for tc in tool_calls)))
        all_results.extend(results)

        # ── Build tool-result messages for the next round ────────────
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

            messages.append({
                "role": "tool",
                "tool_call_id": res["tool_call_id"],
                "content": content,
            })

        # ── Audit logging (fire-and-forget, skip read-only tools) ─────
        _FETCH_TOOLS = {
            "search_menu", "search_group", "search_grant",
            "get_user_current_permissions", "get_menu_by_url",
        }
        for res in results:
            if res["tool"] not in _FETCH_TOOLS:
                asyncio.create_task(
                    db_service.log_ai_request(
                        user_id=user_id,
                        prompt=prompt,
                        reason=reason,
                        tool_name=res["tool"],
                        tool_args=res["args"],
                        result_summary=res["status"],
                    )
                )
    else:
        logger.warning("Reached MAX_TOOL_ROUNDS=%d, forcing stop", MAX_TOOL_ROUNDS)

    return {
        "id": completion_id,
        "tool_calls": [r for r in all_results if r["tool"] not in _FETCH_TOOLS],
        "ai_message": message.content,
    }
