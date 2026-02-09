"""
Database service — real asyncpg calls to PostgreSQL.

Real PL/pgSQL behaviour (verified from source):
─────────────────────────────────────────────────
  employee_group_link(group_id_, employee_id_)
    → TOGGLE: count in employee_group_tab → INSERT or DELETE
    → also checks group_tab.is_active (raises if inactive)

  employee_menu__add(employee_id_, menu_id_)
    → plain INSERT into employee_menu_tab (safe, not toggle)

  employee_module_link(module_id_, employee_id_)
    → TOGGLE: count in employee_module_tab → INSERT or DELETE

  employee_grant_add — НЕ СУЩЕСТВУЕТ как PG-функция.
    → мы делаем прямой INSERT INTO admin.employee_grant_tab
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.database import get_pool

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# FETCH: текущие права сотрудника
# ═══════════════════════════════════════════════════════════════════════════


async def get_user_permissions(employee_id: int) -> dict[str, list[int]]:
    """
    Return every permission the employee currently has (4 parallel SELECTs).
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        groups, menus, modules, grants = await _fetch_all_permissions(conn, employee_id)

    logger.info(
        "get_user_permissions  employee_id=%s  groups=%d menus=%d modules=%d grants=%d",
        employee_id,
        len(groups),
        len(menus),
        len(modules),
        len(grants),
    )
    return {
        "group_ids": groups,
        "menu_ids": menus,
        "module_ids": modules,
        "grant_ids": grants,
    }


async def _fetch_all_permissions(
    conn: Any, employee_id: int
) -> tuple[list[int], list[int], list[int], list[int]]:
    """Run 4 permission queries inside a single connection."""
    group_rows = await conn.fetch(
        "SELECT group_id FROM admin.employee_group_tab WHERE employee_id = $1",
        employee_id,
    )
    menu_rows = await conn.fetch(
        "SELECT menu_id FROM admin.employee_menu_tab WHERE employee_id = $1",
        employee_id,
    )
    module_rows = await conn.fetch(
        "SELECT module_id FROM admin.employee_module_tab WHERE employee_id = $1",
        employee_id,
    )
    grant_rows = await conn.fetch(
        "SELECT grant_id FROM admin.employee_grant_tab WHERE employee_id = $1",
        employee_id,
    )
    return (
        [r["group_id"] for r in group_rows],
        [r["menu_id"] for r in menu_rows],
        [r["module_id"] for r in module_rows],
        [r["grant_id"] for r in grant_rows],
    )


# ═══════════════════════════════════════════════════════════════════════════
# ACTION 1: Назначение группы / роли
#   PG-функция:  admin.employee_group_link(group_id_, employee_id_)
#   Поведение:   TOGGLE (INSERT if absent, DELETE if present)
#   Smart Assign: перед вызовом проверяем count, вызываем ТОЛЬКО если 0
# ═══════════════════════════════════════════════════════════════════════════


async def employee_group_link(employee_id: int, group_id: int) -> None:
    """
    Назначить сотруднику группу (роль).

    ⚠ PG-функция — Toggle: повторный вызов УДАЛИТ роль.
    Smart Assign: проверяем существование перед вызовом.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT count(1) FROM admin.employee_group_tab "
            "WHERE employee_id = $1 AND group_id = $2",
            employee_id,
            group_id,
        )
        if exists:
            logger.warning(
                "employee_group_link SKIPPED (already assigned)  "
                "employee_id=%s group_id=%s",
                employee_id,
                group_id,
            )
            return

        await conn.fetchval(
            "SELECT admin.employee_group_link("
            "  group_id_ := $1, employee_id_ := $2"
            ")",
            group_id,
            employee_id,
        )

    logger.info(
        "employee_group_link OK  employee_id=%s group_id=%s",
        employee_id,
        group_id,
    )


# ═══════════════════════════════════════════════════════════════════════════
# ACTION 2: Добавление пункта меню
#   PG-функция:  admin.employee_menu__add(employee_id_, menu_id_)
#   Поведение:   plain INSERT (безопасное, НЕ toggle)
# ═══════════════════════════════════════════════════════════════════════════


async def employee_menu_add(employee_id: int, menu_id: int) -> None:
    """
    Открыть сотруднику доступ к кнопке/пункту меню.
    Безопасное добавление — функция делает только INSERT.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.fetchval(
            "SELECT admin.employee_menu__add("
            "  employee_id_ := $1, menu_id_ := $2"
            ")",
            employee_id,
            menu_id,
        )

    logger.info(
        "employee_menu__add OK  employee_id=%s menu_id=%s",
        employee_id,
        menu_id,
    )


# ═══════════════════════════════════════════════════════════════════════════
# ACTION 3: Привязка модуля
#   PG-функция:  admin.employee_module_link(module_id_, employee_id_)
#   Поведение:   TOGGLE (INSERT if absent, DELETE if present)
#   Smart Assign: перед вызовом проверяем count, вызываем ТОЛЬКО если 0
# ═══════════════════════════════════════════════════════════════════════════


async def employee_module_link(employee_id: int, module_id: int) -> None:
    """
    Дать сотруднику доступ к модулю (CRM, Склад, Офис…).

    ⚠ PG-функция — Toggle: повторный вызов ОТКЛЮЧИТ модуль.
    Smart Assign: проверяем существование перед вызовом.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT count(1) FROM admin.employee_module_tab "
            "WHERE employee_id = $1 AND module_id = $2",
            employee_id,
            module_id,
        )
        if exists:
            logger.warning(
                "employee_module_link SKIPPED (already assigned)  "
                "employee_id=%s module_id=%s",
                employee_id,
                module_id,
            )
            return

        await conn.fetchval(
            "SELECT admin.employee_module_link("
            "  module_id_ := $1, employee_id_ := $2"
            ")",
            module_id,
            employee_id,
        )

    logger.info(
        "employee_module_link OK  employee_id=%s module_id=%s",
        employee_id,
        module_id,
    )


# ═══════════════════════════════════════════════════════════════════════════
# ACTION 4: Выдача гранта (точечного права)
#   PG-функция:  НЕ СУЩЕСТВУЕТ — делаем прямой INSERT
#   Поведение:   plain INSERT (безопасное, НЕ toggle)
# ═══════════════════════════════════════════════════════════════════════════


async def employee_grant_add(employee_id: int, grant_id: int) -> None:
    """
    Выдать сотруднику точечное право (grant).

    ⚠ PG-функции employee_grant_add НЕТ в базе — прямой INSERT.
    ON CONFLICT DO NOTHING для идемпотентности.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO admin.employee_grant_tab (employee_id, grant_id) "
            "VALUES ($1, $2) "
            "ON CONFLICT DO NOTHING",
            employee_id,
            grant_id,
        )

    logger.info(
        "employee_grant_add OK  employee_id=%s grant_id=%s",
        employee_id,
        grant_id,
    )


# ═══════════════════════════════════════════════════════════════════════════
# CONTEXT: поиск menu_id по URL
#   PG-функция:  admin.get_menu_by_url(url_)
#   Возвращает:  integer (menu_id)
# ═══════════════════════════════════════════════════════════════════════════


async def get_menu_by_url(url: str) -> int | None:
    """Найти menu_id по URL-адресу страницы интерфейса."""
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.fetchval(
            "SELECT admin.get_menu_by_url(url_ := $1)",
            url,
        )

    logger.info("get_menu_by_url  url=%r  result=%s", url, result)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# AUDIT: логирование AI-запросов
# ═══════════════════════════════════════════════════════════════════════════


async def log_ai_request(
    *,
    user_id: int,
    prompt: str,
    reason: str,
    tool_name: str | None,
    tool_args: dict[str, Any] | None,
    result_summary: str | None,
) -> None:
    """Log an AI interaction event, but do not save to database."""
    logger.info(
        "log_ai_request  user_id=%s  tool=%s  prompt=%r  reason=%r  tool_args=%r  status=%s",
        user_id,
        tool_name,
        prompt,
        reason,
        tool_args,
        result_summary,
    )
