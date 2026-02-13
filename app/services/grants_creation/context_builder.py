"""
Сборка контекста для ИИ из admin.grant_tab и дерева прав.

План:
  - SELECT все права из admin.grant_tab (grant_id, grant_code, grant_name, grant_pid, module_id, ...).
  - Распарсить tree_input (дерево вида M__Tier → M__TierBlock → M__TierBlockRead).
  - Сопоставить: какие коды уже есть в БД (с grant_id), какие нужно создать (новые).
  - Вернуть dict для промпта: existing_grants[], new_grants[], структура дерева.

Пока — заглушка. Реализация будет использовать app.data.reference или прямой запрос к БД.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def build_grants_context(tree_input: str) -> dict[str, Any]:
    """
    По дереву прав и данным из admin.grant_tab собрать контекст для LLM.

    Возвращает dict вида:
      existing_grants: [{ grant_id, grant_code, grant_name, ... }, ...]
      new_grants: [{ grant_code, grant_name, parent_code?, ... }, ...]
      tree_summary: str (нормализованное дерево для подсказки ИИ)

    TODO: реализовать SELECT + парсинг дерева.
    """
    logger.info("build_grants_context placeholder  tree_input_len=%d", len(tree_input))
    return {
        "existing_grants": [],
        "new_grants": [],
        "tree_summary": tree_input.strip()[:500],
    }
