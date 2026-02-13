"""
Grants creation pipeline (placeholder).

План:
  1. Принять input (дерево прав + список сотрудников).
  2. SELECT по admin.grant_tab, сопоставить с деревом (существующие grant_id, новые).
  3. Собрать dict-контекст для ИИ.
  4. Вызвать GPT через app.services.llm.create_chat_completion.
  5. ИИ возвращает INSERT SQL для grant_tab и employee_grant_tab.
  6. Выполнить SQL (с проверками).

Пока — заглушка, чтобы не смешивать с process_user_request.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def process_grants_creation_request(
    *,
    tree_input: str,
    employee_names_or_ids: list[str | int],
) -> dict[str, Any]:
    """
    Обработка запроса на создание прав по дереву и выдачу доступа сотрудникам.

    tree_input: текст дерева (например M__Tier (Тир) \\n ├── M__TierBlock ...).
    employee_names_or_ids: список ФИО или employee_id для выдачи прав.

    TODO: реализовать полный пайплайн (context builder → LLM → SQL execution).
    """
    logger.info(
        "grants_creation placeholder  tree_input_len=%d  employees=%s",
        len(tree_input),
        employee_names_or_ids,
    )
    return {
        "status": "not_implemented",
        "message": "Grants creation service is prepared; implementation pending.",
        "tree_input_preview": tree_input[:200] + "..." if len(tree_input) > 200 else tree_input,
        "employee_count": len(employee_names_or_ids),
    }
