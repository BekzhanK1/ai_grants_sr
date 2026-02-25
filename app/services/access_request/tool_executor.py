"""
Tool executor — dispatches OpenAI tool-call objects to db_service functions (access_request flow).

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
    return await db_service.search_menu(
        query=str(args["query"]),
        employee_id=args.get("_employee_id_context"),
    )


async def _handle_search_group(args: dict[str, Any]) -> list[dict[str, Any]]:
    return await db_service.search_group(
        query=str(args["query"]),
        employee_id=args.get("_employee_id_context"),
    )


async def _handle_search_grant(args: dict[str, Any]) -> list[dict[str, Any]]:
    return await db_service.search_grant(
        query=str(args["query"]),
        employee_id=args.get("_employee_id_context"),
    )


# ── Fetch handlers (read-only, return data) ──────────────────────────────────


async def _handle_get_permissions(args: dict[str, Any]) -> dict[str, Any]:
    return await db_service.get_user_permissions(employee_id=int(args["employee_id"]))


async def _handle_get_menu_by_url(args: dict[str, Any]) -> dict[str, Any]:
    menu_id = await db_service.get_menu_by_url(url=str(args["url"]))
    return {"menu_id": menu_id}


# ── Action handlers (write, side effects) ────────────────────────────────────


async def _handle_assign_role(args: dict[str, Any]) -> dict[str, Any]:
    group_id = int(args["group_id"])
    if is_group_blocked(group_id):
        raise PermissionError(
            f"Назначение группы group_id={group_id} заблокировано политикой безопасности. "
            "Администраторские роли нельзя выдавать через AI-интерфейс."
        )
    return await db_service.employee_group_link(
        employee_id=int(args["employee_id"]),
        group_id=group_id,
    )


async def _handle_add_interface_button(args: dict[str, Any]) -> dict[str, Any]:
    menu_id = int(args["menu_id"])
    if is_menu_blocked(menu_id):
        raise PermissionError(
            f"Доступ к меню menu_id={menu_id} заблокирован политикой безопасности. "
            "Меню администрирования нельзя выдавать через AI-интерфейс."
        )
    return await db_service.employee_menu_add(
        employee_id=int(args["employee_id"]),
        menu_id=menu_id,
    )


async def _handle_link_module(args: dict[str, Any]) -> dict[str, Any]:
    return await db_service.employee_module_link(
        employee_id=int(args["employee_id"]),
        module_id=int(args["module_id"]),
    )


async def _handle_add_grant(args: dict[str, Any]) -> dict[str, Any]:
    grant_id = int(args["grant_id"])
    if is_grant_blocked(grant_id):
        raise PermissionError(
            f"Грант grant_id={grant_id} заблокирован политикой безопасности."
        )
    return await db_service.employee_grant_add(
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


async def execute_tool_call(call: Any, user_id: int) -> dict[str, Any]:
    """
    Parse a single OpenAI tool-call object, dispatch to the right handler,
    and return a structured result dict.

    For data tools the result includes a "data" key with the fetched payload.
    For action tools the result includes a "sql" key with the executed query.
    """
    name = call.function.name
    call_id = call.id
    try:
        args: dict[str, Any] = json.loads(call.function.arguments or "{}")
    except json.JSONDecodeError:
        args = {}

    # Inject context for search tools
    args["_employee_id_context"] = user_id

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
        elif name not in _DATA_TOOLS and isinstance(handler_result, dict):
            # It's an action result with sql + entity info
            result["sql"] = handler_result.get("sql")
            result["entity_name"] = handler_result.get("entity_name")
            result["entity_id"] = handler_result.get("entity_id")
        elif name not in _DATA_TOOLS and isinstance(handler_result, str):
            # Legacy string return (just in case)
            result["sql"] = handler_result
    except PermissionError as exc:
        result["status"] = "blocked"
        result["error"] = str(exc)
        logger.warning("Safety block on tool %s: %s", name, exc)
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["error"] = str(exc)
        logger.exception("Tool %s failed", name)

    return result


async def execute_tool_call_preview(call: Any, user_id: int) -> dict[str, Any]:
    """
    Preview variant of execute_tool_call used for access-request \"prepare\" step.

    - SEARCH/FETCH tools работают как обычно (читают из БД, без сайд-эффектов).
    - ACTION tools НЕ выполняют записи в БД, а только возвращают
      планируемое действие и SQL, который будет выполнен на шаге execute.
    """
    name = call.function.name
    call_id = call.id
    try:
        args: dict[str, Any] = json.loads(call.function.arguments or "{}")
    except json.JSONDecodeError:
        args = {}

    # Inject context for search tools
    args["_employee_id_context"] = user_id

    result: dict[str, Any] = {
        "tool_call_id": call_id,
        "tool": name,
        "args": args,
        # Для превью помечаем действие как \"pending\" (ещё не выполнено)
        "status": "pending" if name not in _DATA_TOOLS else "ok",
    }

    # Data tools — исполняем как обычно и возвращаем payload
    if name in _DATA_TOOLS:
        handler = _TOOL_HANDLERS.get(name)
        if handler is None:
            result["status"] = "ignored"
            result["error"] = f"Unknown tool: {name}"
            logger.warning("Unknown tool requested by model (preview): %s", name)
            return result
        try:
            handler_result = await handler(args)
            if handler_result is not None:
                result["data"] = handler_result
        except Exception as exc:  # noqa: BLE001
            result["status"] = "error"
            result["error"] = str(exc)
            logger.exception("Preview data tool %s failed", name)
        return result

    # Action tools — только строим SQL, без фактического вызова db_service.*
    try:
        employee_id = int(args.get("employee_id"))
        sql = None
        entity_id = None
        entity_name = None

        if name == "assign_role":
            group_id = int(args["group_id"])
            sql = (
                "SELECT admin.employee_group_link("
                f"group_id_ := {group_id}, employee_id_ := {employee_id})"
            )
            entity_id = group_id
        elif name == "add_interface_button":
            menu_id = int(args["menu_id"])
            sql = (
                "SELECT admin.employee_menu__add("
                f"employee_id_ := {employee_id}, menu_id_ := {menu_id})"
            )
            entity_id = menu_id
        elif name == "link_module":
            module_id = int(args["module_id"])
            sql = (
                "SELECT admin.employee_module_link("
                f"module_id_ := {module_id}, employee_id_ := {employee_id})"
            )
            entity_id = module_id
        elif name == "add_grant":
            grant_id = int(args["grant_id"])
            sql = (
                "INSERT INTO admin.employee_grant_tab (employee_id, grant_id) "
                f"VALUES ({employee_id}, {grant_id}) ON CONFLICT DO NOTHING"
            )
            entity_id = grant_id
        else:
            # Неизвестный action-инструмент — помечаем как ignored
            result["status"] = "ignored"
            result["error"] = f"Unknown action tool for preview: {name}"
            logger.warning("Unknown action tool requested in preview: %s", name)
            return result

        result["sql"] = sql
        result["entity_id"] = entity_id
        # Resolve human-readable name for preview list
        if name == "add_interface_button":
            result["entity_name"] = await db_service.get_menu_display_name(menu_id)
        elif name == "add_grant":
            result["entity_name"] = await db_service.get_grant_display_name(grant_id)
        elif name == "assign_role":
            result["entity_name"] = await db_service.get_group_display_name(group_id)
        elif name == "link_module":
            result["entity_name"] = await db_service.get_module_display_name(module_id)
        else:
            result["entity_name"] = entity_name
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["error"] = str(exc)
        logger.exception("Preview action tool %s failed", name)

    return result
