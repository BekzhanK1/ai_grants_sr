"""
Tool executor — dispatches OpenAI tool-call objects to db_service functions.

Safety layer:
  - Blocked IDs (admin groups, admin menus) are rejected before DB call.
  - Fetch/search tools return data without side effects.

Tool → DB mapping:
  SEARCH:
    search_menu                   → db_service.search_menu
    search_group                  → db_service.search_group
    search_grant                  → db_service.search_grant
  FETCH:
    get_user_current_permissions  → db_service.get_user_permissions
    get_menu_by_url               → db_service.get_menu_by_url
  ACTION:
    assign_role                   → db_service.employee_group_link  (PG toggle)
    add_interface_button          → db_service.employee_menu_add    (PG insert)
    link_module                   → db_service.employee_module_link (PG toggle)
    add_grant                     → db_service.employee_grant_add   (direct INSERT)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.safety import is_grant_blocked, is_group_blocked, is_menu_blocked
from app.services import db_service

logger = logging.getLogger(__name__)


# ── Search handlers (read-only, return data) ─────────────────────────────────


async def _handle_search_menu(args: dict[str, Any]) -> list[dict[str, Any]]:
    return await db_service.search_menu(query=str(args["query"]))


async def _handle_search_group(args: dict[str, Any]) -> list[dict[str, Any]]:
    return await db_service.search_group(query=str(args["query"]))


async def _handle_search_grant(args: dict[str, Any]) -> list[dict[str, Any]]:
    return await db_service.search_grant(query=str(args["query"]))


# ── Fetch handlers (read-only, return data) ──────────────────────────────────


async def _handle_get_permissions(args: dict[str, Any]) -> dict[str, Any]:
    return await db_service.get_user_permissions(employee_id=int(args["employee_id"]))


async def _handle_get_menu_by_url(args: dict[str, Any]) -> dict[str, Any]:
    menu_id = await db_service.get_menu_by_url(url=str(args["url"]))
    return {"menu_id": menu_id}


# ── Action handlers (write, side effects) ────────────────────────────────────


async def _handle_assign_role(args: dict[str, Any]) -> None:
    group_id = int(args["group_id"])
    if is_group_blocked(group_id):
        raise PermissionError(
            f"Назначение группы group_id={group_id} заблокировано политикой безопасности. "
            "Администраторские роли нельзя выдавать через AI-интерфейс."
        )
    await db_service.employee_group_link(
        employee_id=int(args["employee_id"]),
        group_id=group_id,
    )


async def _handle_add_interface_button(args: dict[str, Any]) -> None:
    menu_id = int(args["menu_id"])
    if is_menu_blocked(menu_id):
        raise PermissionError(
            f"Доступ к меню menu_id={menu_id} заблокирован политикой безопасности. "
            "Меню администрирования нельзя выдавать через AI-интерфейс."
        )
    await db_service.employee_menu_add(
        employee_id=int(args["employee_id"]),
        menu_id=menu_id,
    )


async def _handle_link_module(args: dict[str, Any]) -> None:
    await db_service.employee_module_link(
        employee_id=int(args["employee_id"]),
        module_id=int(args["module_id"]),
    )


async def _handle_add_grant(args: dict[str, Any]) -> None:
    grant_id = int(args["grant_id"])
    if is_grant_blocked(grant_id):
        raise PermissionError(
            f"Грант grant_id={grant_id} заблокирован политикой безопасности."
        )
    await db_service.employee_grant_add(
        employee_id=int(args["employee_id"]),
        grant_id=grant_id,
    )


# ── Registry ─────────────────────────────────────────────────────────────────

_TOOL_HANDLERS: dict[str, Any] = {
    # Search
    "search_menu": _handle_search_menu,
    "search_group": _handle_search_group,
    "search_grant": _handle_search_grant,
    # Fetch
    "get_user_current_permissions": _handle_get_permissions,
    "get_menu_by_url": _handle_get_menu_by_url,
    # Action
    "assign_role": _handle_assign_role,
    "add_interface_button": _handle_add_interface_button,
    "link_module": _handle_link_module,
    "add_grant": _handle_add_grant,
}

# Tools that return data (their result goes back to the model as content)
_DATA_TOOLS: frozenset[str] = frozenset({
    "search_menu",
    "search_group",
    "search_grant",
    "get_user_current_permissions",
    "get_menu_by_url",
})


# ── Public API ───────────────────────────────────────────────────────────────


async def execute_tool_call(call: Any) -> dict[str, Any]:
    """
    Parse a single OpenAI tool-call object, dispatch to the right handler,
    and return a structured result dict.

    For data tools the result includes a "data" key with the fetched payload.
    """
    name = call.function.name
    call_id = call.id
    try:
        args: dict[str, Any] = json.loads(call.function.arguments or "{}")
    except json.JSONDecodeError:
        args = {}

    result: dict[str, Any] = {
        "tool_call_id": call_id,
        "tool": name,
        "args": args,
        "status": "ok",
    }

    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        result["status"] = "ignored"
        result["error"] = f"Unknown tool: {name}"
        logger.warning("Unknown tool requested by model: %s", name)
        return result

    try:
        handler_result = await handler(args)
        if name in _DATA_TOOLS and handler_result is not None:
            result["data"] = handler_result
    except PermissionError as exc:
        result["status"] = "blocked"
        result["error"] = str(exc)
        logger.warning("Safety block on tool %s: %s", name, exc)
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["error"] = str(exc)
        logger.exception("Tool %s failed", name)

    return result
