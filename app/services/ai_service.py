"""
AI service — orchestrates OpenAI chat completion with tool calling.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings
from app.core.exceptions import AIServiceError
from app.data.reference import ReferenceData
from app.services import db_service
from app.services.tool_definitions import build_tools
from app.services.tool_executor import execute_tool_call

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

SYSTEM_PROMPT = (
    "Ты — AI-администратор прав доступа в системе Smart Remont. "
    "Тебе дан user_id сотрудника (employee_id) и естественно-языковой запрос. "
    "Определи, какие права нужно выдать, и вызови подходящие инструменты. "
    "Можешь вызвать несколько инструментов за один ответ."
)


async def process_user_request(
    *,
    user_id: int,
    prompt: str,
    reference_data: ReferenceData,
) -> dict[str, Any]:
    """
    End-to-end pipeline:
    1. Build tools from reference data.
    2. Call OpenAI with system + user messages.
    3. Execute every returned tool call.
    4. Fire-and-forget audit logging.
    5. Return structured result.
    """
    tools = build_tools(reference_data)

    logger.info("Processing request  user_id=%s  prompt=%r", user_id, prompt[:120])

    # ── 1. Call OpenAI ───────────────────────────────────────────────────
    try:
        completion = await _client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            temperature=0.0,
            max_tokens=512,
            tools=tools,
            tool_choice="auto",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"user_id: {user_id}\nзапрос: {prompt}"},
            ],
        )
    except Exception as exc:
        raise AIServiceError(f"OpenAI API error: {exc}") from exc

    message = completion.choices[0].message
    tool_calls = message.tool_calls or []

    logger.info("OpenAI returned %d tool call(s)", len(tool_calls))

    # ── 2. Execute tool calls ────────────────────────────────────────────
    results: list[dict[str, Any]] = []
    if tool_calls:
        results = list(await asyncio.gather(*(execute_tool_call(tc) for tc in tool_calls)))

    # ── 3. Audit logging (fire-and-forget) ───────────────────────────────
    for res in results:
        asyncio.create_task(
            db_service.log_ai_request(
                user_id=user_id,
                prompt=prompt,
                tool_name=res["tool"],
                tool_args=res["args"],
                result_summary=res["status"],
            )
        )

    return {
        "id": completion.id,
        "tool_calls": results,
        "ai_message": message.content,
    }
