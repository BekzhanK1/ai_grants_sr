"""
In-memory reference data with TTL-based auto-refresh from PostgreSQL.

On startup the data is fetched from the DB once.  Subsequent calls to
``get_reference_data()`` return the cached version until the TTL expires,
at which point the next call transparently refreshes the cache.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.database import get_pool

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ReferenceData:
    """Holds menus, groups and grants fetched from Smart Remont DB."""

    menus: list[dict[str, Any]] = field(default_factory=list)
    groups: list[dict[str, Any]] = field(default_factory=list)
    grants: list[dict[str, Any]] = field(default_factory=list)


def _build_tree(
    rows: list[dict[str, Any]],
    *,
    id_key: str,
    pid_key: str,
    label_fn,
    max_nodes: int = 500,
) -> str:
    """
    Generic helper: строит дерево по *_id / *_pid c текстовыми отступами.

    max_nodes ограничивает размер для промпта LLM.
    """
    nodes: dict[Any, dict[str, Any]] = {}
    children: dict[Any, list[Any]] = {}

    for row in rows:
        node_id = row.get(id_key)
        if node_id is None:
            continue
        nodes[node_id] = row
        children.setdefault(node_id, [])

    roots: list[Any] = []
    for row in rows:
        node_id = row.get(id_key)
        parent_id = row.get(pid_key)
        if node_id is None:
            continue
        if parent_id and parent_id in nodes:
            children.setdefault(parent_id, []).append(node_id)
        else:
            roots.append(node_id)

    lines: list[str] = []

    def dfs(node_id: Any, level: int, counter: list[int]) -> None:
        if counter[0] >= max_nodes:
            return
        row = nodes.get(node_id)
        if not row:
            return
        indent = "  " * level
        lines.append(f"{indent}- {label_fn(row)}")
        counter[0] += 1
        for child_id in children.get(node_id, []):
            if counter[0] >= max_nodes:
                break
            dfs(child_id, level + 1, counter)

    counter = [0]
    for rid in roots:
        if counter[0] >= max_nodes:
            break
        dfs(rid, 0, counter)

    if counter[0] >= max_nodes:
        lines.append("")
        lines.append(
            f"... (обрезано по лимиту {max_nodes} узлов для промпта LLM)"
        )

    return "\n".join(lines)


async def build_permissions_tree_context() -> str:
    """
    Собирает текстовый контекст для LLM из меню и прав:
    - Дерево меню (по menu_pid)
    - Дерево прав (по grant_pid), сгруппированное по system_block / module

    Модули и группы в явном виде не перечисляем — только меню и гранты.
    """
    ref = await get_reference_data()

    menu_tree = _build_tree(
        ref.menus,
        id_key="menu_id",
        pid_key="menu_pid",
        label_fn=lambda r: f"[{r.get('location')}] {r.get('menu_name')} "
        f"(id={r.get('menu_id')}, code={r.get('menu_code')})",
    )

    # Гранты группируем по system_block (если есть)
    grants_by_block: dict[str, list[dict[str, Any]]] = {}
    for g in ref.grants:
        block = (g.get("system_block") or "UNSPECIFIED").strip() or "UNSPECIFIED"
        grants_by_block.setdefault(block, []).append(g)

    grant_lines: list[str] = []
    for block, rows in sorted(grants_by_block.items(), key=lambda x: x[0]):
        grant_lines.append(f"[Блок: {block}]")
        tree = _build_tree(
            rows,
            id_key="grant_id",
            pid_key="grant_pid",
            label_fn=lambda r: f"{r.get('grant_name')} "
            f"(id={r.get('grant_id')}, code={r.get('grant_code')})",
            max_nodes=300,
        )
        if tree:
            grant_lines.append(tree)
        grant_lines.append("")

    grants_tree = "\n".join(grant_lines).strip()

    return (
        "СПРАВОЧНИК ПРАВ (для поиска по смыслу):\n\n"
        "МЕНЮ:\n"
        f"{menu_tree}\n\n"
        "ПРАВА (GRANTS):\n"
        f"{grants_tree}"
    )


# ── Singleton + TTL cache ────────────────────────────────────────────────────

_cache: ReferenceData | None = None
_loaded_at: float = 0.0
_lock: asyncio.Lock = asyncio.Lock()


async def load_reference_data() -> ReferenceData:
    """
    Fetch reference data from the DB.

    Thread-safe: uses an asyncio.Lock so concurrent requests don't
    trigger multiple refreshes.
    """
    global _cache, _loaded_at  # noqa: PLW0603

    async with _lock:
        # Double-check: another coroutine may have refreshed while we waited
        if _cache is not None and not _is_expired():
            return _cache

        pool = get_pool()
        async with pool.acquire() as conn:
            # 1. Группы (Роли) с контекстом модуля
            group_rows = await conn.fetch(
                "SELECT g.group_id, g.group_name, g.group_code, m.module_name as context "
                "FROM admin.group_tab g "
                "LEFT JOIN admin.module_tab m ON g.module_id = m.module_id "
                "WHERE g.is_active = true "
                "ORDER BY g.group_id"
            )

            # 2. Меню с правильным pid и фильтром активности
            menu_rows = await conn.fetch(
                "SELECT t.menu_id, t.menu_pid, t.menu_name, t.menu_code, t.menu_action, mod.module_name as location "
                "FROM admin.menu_tab t "
                "JOIN admin.module_tab mod ON t.module_id = mod.module_id "
                "WHERE t.is_active = 1 "
                "ORDER BY t.menu_id"
            )

            # 3. Гранты с правильным pid и привязкой к блокам системы
            grant_rows = await conn.fetch(
                "SELECT g.grant_id, g.grant_pid, g.grant_name, g.grant_code, mod.module_name as system_block "
                "FROM admin.grant_tab g "
                "LEFT JOIN admin.module_tab mod ON g.module_id = mod.module_id "
                "WHERE g.is_active = 1 "
                "ORDER BY g.grant_id"
            )

        groups = [dict(r) for r in group_rows]
        menus = [dict(r) for r in menu_rows]
        grants = [dict(r) for r in grant_rows]

        _cache = ReferenceData(menus=menus, groups=groups, grants=grants)
        _loaded_at = time.monotonic()

        logger.info(
            "Reference data loaded from DB: %d menus, %d groups, %d grants  (TTL=%ds)",
            len(menus),
            len(groups),
            len(grants),
            settings.REFERENCE_TTL,
        )
        return _cache


async def get_reference_data() -> ReferenceData:
    """
    Return cached reference data, refreshing from DB if TTL has expired.

    Safe to call from any request handler — the lock prevents stampedes.
    """
    if _cache is not None and not _is_expired():
        return _cache
    return await load_reference_data()


def _is_expired() -> bool:
    if settings.REFERENCE_TTL <= 0:
        return False  # TTL=0 means "never auto-refresh"
    return (time.monotonic() - _loaded_at) > settings.REFERENCE_TTL
