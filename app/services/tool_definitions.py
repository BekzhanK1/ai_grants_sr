"""
OpenAI function-calling tool definitions.

Each tool maps to a stored procedure / query in Smart Remont.
Reference data is injected into descriptions so the model can resolve
natural-language names to concrete IDs.

Tool categories:
  FETCH (read-only, no side effects):
    get_user_current_permissions  → 4 × SELECT from permission tables
    get_menu_by_url               → admin.get_menu_by_url(url_)

  ACTION (write):
    assign_role                   → admin.employee_group_link   TOGGLE
    add_interface_button          → admin.employee_menu__add    INSERT
    link_module                   → admin.employee_module_link  TOGGLE
    add_grant                     → INSERT INTO employee_grant_tab
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
        # ─────────────────────────────────────────────────────────────────
        #  FETCH tools (read-only)
        # ─────────────────────────────────────────────────────────────────

        _function_tool(
            name="get_user_current_permissions",
            description=(
                "ВСЕГДА вызывай этот инструмент ПЕРВЫМ перед любыми действиями. "
                "Возвращает списки всех group_id, menu_id, module_id и grant_id, "
                "которые УЖЕ назначены сотруднику. Используй результат, чтобы "
                "не назначать то, что уже есть (Toggle Trap)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "employee_id": {"type": "integer", "description": "ID сотрудника"},
                },
                "required": ["employee_id"],
            },
        ),
        _function_tool(
            name="get_menu_by_url",
            description=(
                "Найти menu_id по URL-адресу страницы интерфейса. "
                "Если пользователь скинул ссылку вместо ID — используй этот инструмент."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL или путь страницы, например /system/employee-list/",
                    },
                },
                "required": ["url"],
            },
        ),

        # ─────────────────────────────────────────────────────────────────
        #  ACTION tools (write)
        # ─────────────────────────────────────────────────────────────────

        _function_tool(
            name="assign_role",
            description=(
                "Назначить сотруднику группу (роль/должность). "
                "⚠ TOGGLE: если роль уже есть — НЕ вызывай, иначе она будет УДАЛЕНА. "
                "Сначала проверь через get_user_current_permissions. "
                f"Доступные группы (group_id → name): "
                f"{json.dumps(groups_map, ensure_ascii=False)[:2000]}"
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
            name="add_interface_button",
            description=(
                "Открыть сотруднику доступ к кнопке/пункту меню интерфейса. "
                "Безопасное добавление (не toggle). "
                "Если menu_id уже есть — сообщи, что доступ уже открыт. "
                f"Доступные пункты (menu_id → name): "
                f"{json.dumps(menus_map, ensure_ascii=False)[:2000]}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "employee_id": {"type": "integer", "description": "ID сотрудника"},
                    "menu_id": {"type": "integer", "description": "ID пункта меню"},
                },
                "required": ["employee_id", "menu_id"],
            },
        ),
        _function_tool(
            name="link_module",
            description=(
                "Дать сотруднику доступ к целому модулю системы "
                "(CRM, Склад, Офис и т.д.). "
                "⚠ TOGGLE: если модуль уже привязан — НЕ вызывай, "
                "иначе он будет ОТКЛЮЧЁН. "
                "Сначала проверь через get_user_current_permissions."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "employee_id": {"type": "integer", "description": "ID сотрудника"},
                    "module_id": {"type": "integer", "description": "ID модуля"},
                },
                "required": ["employee_id", "module_id"],
            },
        ),
        _function_tool(
            name="add_grant",
            description=(
                "Выдать сотруднику точечное техническое право (grant). "
                "Безопасное добавление (не toggle). "
                "Если grant_id уже есть — сообщи пользователю. "
                f"Доступные гранты (grant_id → name): "
                f"{json.dumps(grants_map, ensure_ascii=False)[:2000]}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "employee_id": {"type": "integer", "description": "ID сотрудника"},
                    "grant_id": {"type": "integer", "description": "ID гранта"},
                },
                "required": ["employee_id", "grant_id"],
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
