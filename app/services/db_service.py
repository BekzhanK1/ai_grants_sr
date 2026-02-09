"""
Database service — stub implementations.

Every function logs the call via the standard logger instead of touching
a real database.  To switch to production mode, replace the stub bodies
with actual asyncpg calls (see docstrings for the expected SQL).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── Access-rights operations (stubs) ────────────────────────────────────────


async def employee_group_link(employee_id: int, group_id: int) -> None:
    """
    Link employee → group.

    Production SQL (toggle with safety check):
        SELECT EXISTS(SELECT 1 FROM employee_group_links
                      WHERE employee_id=$1 AND group_id=$2);
        -- only if NOT exists:
        SELECT employee_group_link($1, $2);
    """
    logger.info(
        "[STUB] employee_group_link  employee_id=%s  group_id=%s",
        employee_id,
        group_id,
    )


async def employee_menu_add(employee_id: int, menu_id: int, grant_id: int) -> None:
    """
    Grant menu access to employee (non-toggle, direct add).

    Production SQL:
        SELECT employee_menu__add($1, $2, $3);
    """
    logger.info(
        "[STUB] employee_menu_add   employee_id=%s  menu_id=%s  grant_id=%s",
        employee_id,
        menu_id,
        grant_id,
    )


async def employee_module_link(employee_id: int, module_id: int) -> None:
    """
    Link employee → module.

    Production SQL (toggle with safety check):
        SELECT EXISTS(SELECT 1 FROM employee_module_links
                      WHERE employee_id=$1 AND module_id=$2);
        -- only if NOT exists:
        SELECT employee_module_link($1, $2);
    """
    logger.info(
        "[STUB] employee_module_link  employee_id=%s  module_id=%s",
        employee_id,
        module_id,
    )


# ── Audit logging (stub) ────────────────────────────────────────────────────


async def log_ai_request(
    *,
    user_id: int,
    prompt: str,
    tool_name: str | None,
    tool_args: dict[str, Any] | None,
    result_summary: str | None,
) -> None:
    """
    Persist an AI interaction record.

    Production SQL:
        INSERT INTO ai_request_logs(user_id, prompt, tool_name, tool_args, result_summary)
        VALUES ($1, $2, $3, $4, $5);
    """
    logger.info(
        "[STUB] log_ai_request  user_id=%s  tool=%s  status=%s  args=%s",
        user_id,
        tool_name,
        result_summary,
        tool_args,
    )
