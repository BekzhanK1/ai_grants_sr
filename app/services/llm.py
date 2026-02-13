"""
Shared LLM (OpenAI) client and completion helper.

Used by:
  - ai_service: process_user_request (tool-calling flow)
  - grants_creation: future flow for bulk grant creation from tree input

Single place to call the GPT model so both flows share config and client.
"""

from __future__ import annotations

import logging
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    """Return shared AsyncOpenAI client (lazy init)."""
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


async def create_chat_completion(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | None = "auto",
) -> Any:
    """
    Call OpenAI chat completions API. Used by both process_user_request and grants_creation.

    Returns the raw completion object (has .id, .choices[0].message, etc.).
    """
    client = get_client()
    kwargs: dict[str, Any] = {
        "model": model or settings.OPENAI_MODEL,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if tools is not None:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice
    return await client.chat.completions.create(**kwargs)
