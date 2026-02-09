"""
OpenAI function-calling tool definitions.

Each tool maps to a stored procedure in the Smart Remont database.
Reference data (menus/groups/grants) is injected into descriptions so the
model can resolve natural-language names to concrete IDs.
"""

from __future__ import annotations

import json
from typing import Any

from app.data.reference import ReferenceData


def build_tools(reference_data: ReferenceData) -> list[dict[str, Any]]:
    """Return the list of tool schemas sent to OpenAI chat completions."""
    menus_map = {m["menu_id"]: m["menu_name"] for m in reference_data.menus if "menu_id" in m}
    groups_map = {g["group_id"]: g["group_name"] for g in reference_data.groups if "group_id" in g}
    grants_map = {g["grant_id"]: g["grant_name"] for g in reference_data.grants if "grant_id" in g}

    return [
        _function_tool(
            name="grant_group_access",
            description=(
                "Назначить сотруднику принадлежность к группе доступа. "
                f"Доступные группы: {json.dumps(groups_map, ensure_ascii=False)[:2000]}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "employee_id": {"type": "integer", "description": "ID сотрудника"},
                    "group_id": {"type": "integer", "description": "ID группы"},
                },
                "required": ["employee_id", "group_id"],
            },
        ),
        _function_tool(
            name="add_menu_for_employee",
            description=(
                "Выдать сотруднику доступ к пункту меню с определённым grant. "
                f"Меню: {json.dumps(menus_map, ensure_ascii=False)[:1500]}; "
                f"Grants: {json.dumps(grants_map, ensure_ascii=False)[:1500]}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "employee_id": {"type": "integer", "description": "ID сотрудника"},
                    "menu_id": {"type": "integer", "description": "ID пункта меню"},
                    "grant_id": {"type": "integer", "description": "ID гранта"},
                },
                "required": ["employee_id", "menu_id", "grant_id"],
            },
        ),
        _function_tool(
            name="link_module_for_employee",
            description="Назначить сотруднику доступ к модулю системы.",
            parameters={
                "type": "object",
                "properties": {
                    "employee_id": {"type": "integer", "description": "ID сотрудника"},
                    "module_id": {"type": "integer", "description": "ID модуля"},
                },
                "required": ["employee_id", "module_id"],
            },
        ),
    ]


# ── helpers ──────────────────────────────────────────────────────────────────


def _function_tool(
    *, name: str, description: str, parameters: dict[str, Any]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }
