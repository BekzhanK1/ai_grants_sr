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
