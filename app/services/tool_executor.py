"""
Tool executor — dispatches OpenAI tool-call objects to db_service functions.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.services import db_service

logger = logging.getLogger(__name__)


# ── Handlers (private) ───────────────────────────────────────────────────────


async def _handle_grant_group_access(args: dict[str, Any]) -> None:
    await db_service.employee_group_link(
        employee_id=int(args["employee_id"]),
        group_id=int(args["group_id"]),
    )


async def _handle_add_menu_for_employee(args: dict[str, Any]) -> None:
    await db_service.employee_menu_add(
        employee_id=int(args["employee_id"]),
        menu_id=int(args["menu_id"]),
        grant_id=int(args["grant_id"]),
    )


async def _handle_link_module_for_employee(args: dict[str, Any]) -> None:
    await db_service.employee_module_link(
        employee_id=int(args["employee_id"]),
        module_id=int(args["module_id"]),
    )


# Registry: tool name → async handler
_TOOL_HANDLERS = {
    "grant_group_access": _handle_grant_group_access,
    "add_menu_for_employee": _handle_add_menu_for_employee,
    "link_module_for_employee": _handle_link_module_for_employee,
}


# ── Public API ───────────────────────────────────────────────────────────────


async def execute_tool_call(call: Any) -> dict[str, Any]:
    """
    Parse a single OpenAI tool-call object, dispatch to the right handler,
    and return a structured result dict.
    """
    name = call.function.name
    try:
        args: dict[str, Any] = json.loads(call.function.arguments or "{}")
    except json.JSONDecodeError:
        args = {}

    result: dict[str, Any] = {"tool": name, "args": args, "status": "ok"}

    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        result["status"] = "ignored"
        result["error"] = f"Unknown tool: {name}"
        logger.warning("Unknown tool requested by model: %s", name)
        return result

    try:
        await handler(args)
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["error"] = str(exc)
        logger.exception("Tool %s failed", name)

    return result
