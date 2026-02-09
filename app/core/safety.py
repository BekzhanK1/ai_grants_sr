"""
Safety rules — blacklists of IDs that the AI agent must NEVER assign.

These protect administrator-level access from being granted through the
natural-language interface.  Even if the LLM hallucinates an admin-level
tool call, the executor will block it before it reaches the database.
"""

from __future__ import annotations

# ── Blocked group IDs ────────────────────────────────────────────────────────
# group_id=1 → Администраторы (ADMIN)
BLOCKED_GROUP_IDS: frozenset[int] = frozenset({1})

# ── Blocked menu IDs ────────────────────────────────────────────────────────
# menu_id=1  → Администрирование (parent)
# menu_id=2  → Ведение ролей
# menu_id=4  → Ведение пользователей
# menu_id=10 → Привилегии пользователей
BLOCKED_MENU_IDS: frozenset[int] = frozenset({1, 2, 4, 10})

# ── Blocked grant codes (substring match) ───────────────────────────────────
# You can extend this as needed; currently we block nothing by grant_id
# because grants are granular.  But we block super-admin group above.
BLOCKED_GRANT_IDS: frozenset[int] = frozenset()


def is_group_blocked(group_id: int) -> bool:
    return group_id in BLOCKED_GROUP_IDS


def is_menu_blocked(menu_id: int) -> bool:
    return menu_id in BLOCKED_MENU_IDS


def is_grant_blocked(grant_id: int) -> bool:
    return grant_id in BLOCKED_GRANT_IDS
