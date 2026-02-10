"""
OpenAI function-calling tool definitions.

Each tool maps to a stored procedure / query in Smart Remont.

Tool categories:
  SEARCH (read-only, returns matching items from reference tables):
    search_menu    → ILIKE search in admin.menu_tab
    search_group   → ILIKE search in admin.group_tab
    search_grant   → ILIKE search in admin.grant_tab

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

from typing import Any


def build_tools() -> list[dict[str, Any]]:
    """Return the list of tool schemas sent to OpenAI chat completions."""
    return [
        # ─────────────────────────────────────────────────────────────────
        #  SEARCH tools (look up IDs by name)
        # ─────────────────────────────────────────────────────────────────

        _function_tool(
            name="search_menu",
            description=(
                "Поиск пункта меню по названию. Используй, когда пользователь "
                "указал название меню текстом, а не menu_id. "
                "Возвращает список найденных: [{menu_id, menu_name, parent_id, location}]. "
                "Если несколько результатов — уточни у пользователя, какой именно нужен."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Подстрока для поиска в названии меню",
                    },
                },
                "required": ["query"],
            },
        ),
        _function_tool(
            name="search_group",
            description=(
                "Поиск группы (роли) по названию. Используй, когда пользователь "
                "указал название роли/группы текстом, а не group_id. "
                "Возвращает список найденных: [{group_id, group_name, group_code}]."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Подстрока для поиска в названии группы",
                    },
                },
                "required": ["query"],
            },
        ),
        _function_tool(
            name="search_grant",
            description=(
                "Поиск гранта (точечного права) по названию. Используй, когда "
                "пользователь указал название права текстом, а не grant_id. "
                "Возвращает список найденных: [{grant_id, grant_name, grant_code, system_block}]."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Подстрока для поиска в названии гранта",
                    },
                },
                "required": ["query"],
            },
        ),

        # ─────────────────────────────────────────────────────────────────
        #  FETCH tools (read-only)
        # ─────────────────────────────────────────────────────────────────

        _function_tool(
            name="get_user_current_permissions",
            description=(
                "ВСЕГДА вызывай этот инструмент перед любыми действиями по назначению прав. "
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
                "Если не знаешь group_id — сначала вызови search_group."
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
                "Если не знаешь menu_id — сначала вызови search_menu."
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
                "Если не знаешь grant_id — сначала вызови search_grant."
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
