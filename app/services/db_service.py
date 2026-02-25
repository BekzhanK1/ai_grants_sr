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

import asyncpg
from app.api.schemas import (
    CityDto,
    EmployeeDto,
    EmployeeSearchItem,
    GrantDto,
    ModuleDto,
    PositionDto,
)
from app.core.config import settings
from app.core.database import get_pool

# Visual feedback for TEST_MODE
if settings.TEST_MODE:
    # Yellow
    print(f"\033[93m============TEST_MODE ON============\033[0m (Rollback enabled)")
else:
    # Red
    print(f"\033[91m============TEST_MODE OFF============\033[0m (Commit enabled)")

if settings.DAILY_LIMIT_ON:
    print(f"\033[93m===========DAILY_LIMIT ON==========\033[0m (Daily limit enabled)")
else:
    print(f"\033[91m===========DAILY_LIMIT OFF==========\033[0m (Daily limit disabled)")

if settings.ADMIN_APPROVE:
    print(
        f"\033[93m===========ADMIN_APPROVE ON==========\033[0m (Admin approve enabled)"
    )
else:
    print(
        f"\033[91m===========ADMIN_APPROVE OFF==========\033[0m (Admin approve disabled)"
    )

logger = logging.getLogger(__name__)


def _log_db_response(func_name: str, data: Any) -> None:
    """Log database response to console."""
    print(f"\n{'='*60}")
    print(f"DB RESPONSE from {func_name}")
    print(f"{'='*60}")
    print(data)
    print(f"{'='*60}\n")


async def get_default_office_id_for_company(company_id: int) -> int | None:
    """
    Возвращает дефолтный office_id для компании (для создания офисных пользователей).
    Берём первый офис компании по office_id.
    """
    pool = get_pool()
    try:
        async with pool.acquire() as conn:
            office_id = await conn.fetchval(
                """
                SELECT office_id 
                FROM public.office_tab 
                WHERE company_id = $1
                ORDER BY office_id 
                LIMIT 1
                """,
                company_id,
            )
            return office_id
    except Exception as e:
        logger.warning(
            "get_default_office_id_for_company failed for company_id=%s: %s",
            company_id,
            e,
        )
        return None


async def get_position_info(position_id: int) -> dict[str, Any] | None:
    """
    Возвращает информацию о должности: is_smart, is_sale_point, position_code, module_id.
    """
    pool = get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT position_id, position_name, position_code, is_smart, is_sale_point, module_id
                FROM admin.position_tab
                WHERE position_id = $1
                """,
                position_id,
            )
            return dict(row) if row else None
    except Exception as e:
        logger.warning(
            "get_position_info failed for position_id=%s: %s", position_id, e
        )
        return None


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
    _log_db_response(
        "get_user_permissions",
        {
            "group_ids": groups,
            "menu_ids": menus,
            "module_ids": modules,
            "grant_ids": grants,
        },
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


async def get_employee_current_menus_and_grants(
    employee_id: int,
) -> dict[str, list[int]]:
    """
    Текущие menu_id и grant_id сотрудника без дублей:
    из employee_menu_tab / employee_grant_tab и через группы (group_menu_tab / group_grant_tab).
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        menu_rows = await conn.fetch(
            """
            SELECT menu_id FROM admin.employee_menu_tab WHERE employee_id = $1
            UNION
            SELECT gm.menu_id
            FROM admin.group_menu_tab gm
            INNER JOIN admin.employee_group_tab eg ON eg.group_id = gm.group_id
            WHERE eg.employee_id = $1
            """,
            employee_id,
        )
        grant_rows = await conn.fetch(
            """
            SELECT grant_id FROM admin.employee_grant_tab WHERE employee_id = $1
            UNION
            SELECT gg.grant_id
            FROM admin.group_grant_tab gg
            INNER JOIN admin.employee_group_tab eg ON eg.group_id = gg.group_id
            WHERE eg.employee_id = $1
            """,
            employee_id,
        )
    return {
        "menu_ids": [r["menu_id"] for r in menu_rows],
        "grant_ids": [r["grant_id"] for r in grant_rows],
    }


# ═══════════════════════════════════════════════════════════════════════════
# SEARCH: поиск по справочникам (меню, группы, гранты)
# ═══════════════════════════════════════════════════════════════════════════


async def _get_employee_module_id(conn: Any, employee_id: int) -> int | None:
    """
    Get the 'native' module_id for an employee based on their position.
    Used to prioritize search results.
    """
    try:
        return await conn.fetchval(
            """
            SELECT p.module_id 
            FROM admin.employee_tab e
            JOIN admin.position_tab p ON e.position_id = p.position_id
            WHERE e.employee_id = $1
            """,
            employee_id,
        )
    except Exception:
        logger.warning("Failed to determine module_id for employee %s", employee_id)
        return None


async def search_menu(
    query: str, employee_id: int | None = None
) -> list[dict[str, Any]]:
    """
    Поиск пунктов меню по названию.
    Только активные. Включает parent_name и module_name.
    Нечёткий поиск (pg_trgm), если доступен; иначе ILIKE.
    Приоритет: совпадение модуля (если передан employee_id).
    """
    query = query.strip()
    if not query:
        return []

    pool = get_pool()
    async with pool.acquire() as conn:
        target_module_id = None
        if employee_id:
            target_module_id = await _get_employee_module_id(conn, employee_id)

        # Base SELECT
        select_sql = (
            "SELECT t.menu_id, t.menu_name, t.menu_action, "
            "       p.menu_name AS parent_name, m.module_name, t.module_id "
            "FROM admin.menu_tab t "
            "LEFT JOIN admin.menu_tab p ON t.menu_pid = p.menu_id "
            "LEFT JOIN admin.module_tab m ON t.module_id = m.module_id "
            "WHERE t.is_active = 1 "
        )

        # Ordering logic: boost raw match and module match
        # If target_module_id is set, we use it to boost relevance
        # We need conditional SQL construction or pass parameter conditionally

        args = [query]
        order_clause = ""

        if target_module_id:
            args.append(target_module_id)
            # $2 is target_module_id
            module_boost = "(CASE WHEN t.module_id = $2 THEN 1 ELSE 0 END) DESC, "
        else:
            module_boost = ""

        try:
            # TRY pg_trgm
            where_clause = (
                "AND (t.menu_name ILIKE '%' || $1 || '%' OR t.menu_name % $1) "
            )
            order_clause = f"ORDER BY {module_boost} similarity(t.menu_name, $1) DESC NULLS LAST, t.menu_id LIMIT 15"
            sql = select_sql + where_clause + order_clause
            rows = await conn.fetch(sql, *args)
        except asyncpg.UndefinedFunctionError:
            # Fallback ILIKE
            where_clause = "AND t.menu_name ILIKE '%' || $1 || '%' "
            order_clause = f"ORDER BY {module_boost} t.menu_id LIMIT 15"
            sql = select_sql + where_clause + order_clause
            rows = await conn.fetch(sql, *args)

    results = [dict(r) for r in rows]
    logger.info(
        "search_menu query=%r employee_id=%s found=%d", query, employee_id, len(results)
    )
    _log_db_response("search_menu", results)
    return results


async def search_group(
    query: str,
    employee_id: int | None = None,
    module_id: int | None = None,
) -> list[dict[str, Any]]:
    """
    Поиск групп (ролей) по названию.
    Если передан module_id — только группы этого модуля (для «группу/права из того же модуля»).
    Иначе приоритет по модулю сотрудника (employee_id).
    """
    query = query.strip()
    if not query:
        return []
    pool = get_pool()
    async with pool.acquire() as conn:
        target_module_id: int | None = module_id
        if target_module_id is None and employee_id:
            target_module_id = await _get_employee_module_id(conn, employee_id)

        select_sql = (
            "SELECT g.group_id, g.group_name, g.group_code, m.module_name, g.module_id "
            "FROM admin.group_tab g "
            "LEFT JOIN admin.module_tab m ON g.module_id = m.module_id "
            "WHERE g.is_active = true "
        )
        args: list[Any] = [query]
        if target_module_id is not None:
            args.append(target_module_id)
            select_sql += " AND g.module_id = $2 "

        module_boost = (
            "(CASE WHEN g.module_id = $2 THEN 1 ELSE 0 END) DESC, "
            if target_module_id is not None
            else ""
        )

        try:
            where_clause = (
                "AND (g.group_name ILIKE '%' || $1 || '%' OR g.group_name % $1) "
            )
            order_clause = f"ORDER BY {module_boost} similarity(g.group_name, $1) DESC NULLS LAST, g.group_id LIMIT 15"
            sql = select_sql + where_clause + order_clause
            rows = await conn.fetch(sql, *args)
        except asyncpg.UndefinedFunctionError:
            where_clause = "AND g.group_name ILIKE '%' || $1 || '%' "
            order_clause = f"ORDER BY {module_boost} g.group_id LIMIT 15"
            sql = select_sql + where_clause + order_clause
            rows = await conn.fetch(sql, *args)

    results = [dict(r) for r in rows]
    logger.info(
        "search_group query=%r employee_id=%s module_id=%s found=%d",
        query,
        employee_id,
        module_id,
        len(results),
    )
    _log_db_response("search_group", results)
    return results


async def search_grant(
    query: str,
    employee_id: int | None = None,
    module_id: int | None = None,
) -> list[dict[str, Any]]:
    """
    Поиск грантов по названию.
    Если передан module_id — только гранты этого модуля (для «права из того же модуля»).
    Иначе приоритет по модулю сотрудника (employee_id).
    """
    query = query.strip()
    if not query:
        return []
    pool = get_pool()
    async with pool.acquire() as conn:
        target_module_id: int | None = module_id
        if target_module_id is None and employee_id:
            target_module_id = await _get_employee_module_id(conn, employee_id)

        select_sql = (
            "SELECT g.grant_id, g.grant_name, g.grant_code, "
            "       p.grant_name AS parent_name, m.module_name, g.module_id "
            "FROM admin.grant_tab g "
            "LEFT JOIN admin.grant_tab p ON g.grant_pid = p.grant_id "
            "LEFT JOIN admin.module_tab m ON g.module_id = m.module_id "
            "WHERE g.is_active = 1 "
        )
        args: list[Any] = [query]
        if target_module_id is not None:
            args.append(target_module_id)
            select_sql += " AND g.module_id = $2 "

        module_boost = (
            "(CASE WHEN g.module_id = $2 THEN 1 ELSE 0 END) DESC, "
            if target_module_id is not None
            else ""
        )

        try:
            where_clause = (
                "AND (g.grant_name ILIKE '%' || $1 || '%' OR g.grant_name % $1) "
            )
            order_clause = f"ORDER BY {module_boost} similarity(g.grant_name, $1) DESC NULLS LAST, g.grant_id LIMIT 15"
            sql = select_sql + where_clause + order_clause
            rows = await conn.fetch(sql, *args)
        except asyncpg.UndefinedFunctionError:
            where_clause = "AND g.grant_name ILIKE '%' || $1 || '%' "
            order_clause = f"ORDER BY {module_boost} g.grant_id LIMIT 15"
            sql = select_sql + where_clause + order_clause
            rows = await conn.fetch(sql, *args)

    results = [dict(r) for r in rows]
    logger.info(
        "search_grant query=%r employee_id=%s module_id=%s found=%d",
        query,
        employee_id,
        module_id,
        len(results),
    )
    _log_db_response("search_grant", results)
    return results


# ═══════════════════════════════════════════════════════════════════════════
# ACTION 1: Назначение группы / роли
#   PG-функция:  admin.employee_group_link(group_id_, employee_id_)
#   Поведение:   TOGGLE (INSERT if absent, DELETE if present)
#   Smart Assign: перед вызовом проверяем count, вызываем ТОЛЬКО если 0
# ═══════════════════════════════════════════════════════════════════════════


async def employee_group_link(employee_id: int, group_id: int) -> dict[str, Any]:
    """
    Назначить сотруднику группу (роль).

    ⚠ PG-функция — Toggle: повторный вызов УДАЛИТ роль.
    Smart Assign: проверяем существование перед вызовом.
    Returns: Dict with Executed SQL statement and entity info.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        # Fetch name for logging
        group_name = await conn.fetchval(
            "SELECT group_name FROM admin.group_tab WHERE group_id = $1",
            group_id,
        )
        entity_info = {
            "entity_name": group_name or f"Unknown Group {group_id}",
            "entity_id": group_id,
        }

        exists = await conn.fetchval(
            "SELECT count(1) FROM admin.employee_group_tab "
            "WHERE employee_id = $1 AND group_id = $2",
            employee_id,
            group_id,
        )
        if exists:
            msg = f"-- SKIPPED (already assigned) employee_id={employee_id} group_id={group_id}"
            logger.warning("employee_group_link %s", msg)
            return {"sql": msg, **entity_info}

        query = (
            "SELECT admin.employee_group_link("
            f"group_id_ := {group_id}, employee_id_ := {employee_id})"
        )

        tr = conn.transaction()
        await tr.start()
        try:
            await conn.fetchval(
                "SELECT admin.employee_group_link("
                "  group_id_ := $1, employee_id_ := $2"
                ")",
                group_id,
                employee_id,
            )
            if settings.TEST_MODE:
                await tr.rollback()
                logger.info("TEST_MODE: Rolled back employee_group_link")
                query += " [TEST_MODE: ROLLED BACK]"
            else:
                await tr.commit()
        except Exception:
            await tr.rollback()
            raise

    logger.info(
        "employee_group_link OK  employee_id=%s group_id=%s",
        employee_id,
        group_id,
    )
    return {"sql": query, **entity_info}


# ═══════════════════════════════════════════════════════════════════════════
# ACTION 2: Добавление пункта меню
#   PG-функция:  admin.employee_menu__add(employee_id_, menu_id_)
#   Поведение:   plain INSERT (безопасное, НЕ toggle)
# ═══════════════════════════════════════════════════════════════════════════


async def employee_menu_add(employee_id: int, menu_id: int) -> dict[str, Any]:
    """
    Открыть сотруднику доступ к кнопке/пункту меню.
    Безопасное добавление — функция делает только INSERT.
    Returns: Dict with Executed SQL statement and entity info.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        # Fetch name for logging (with module context)
        row = await conn.fetchrow(
            """
            SELECT t.menu_name, m.module_name 
            FROM admin.menu_tab t
            LEFT JOIN admin.module_tab m ON t.module_id = m.module_id
            WHERE t.menu_id = $1
            """,
            menu_id,
        )
        if row:
            menu_name = row["menu_name"]
            module_name = row["module_name"] or "Unknown Module"
            entity_name = f"{menu_name} ({module_name})"
        else:
            entity_name = f"Unknown Menu {menu_id}"

        entity_info = {
            "entity_name": entity_name,
            "entity_id": menu_id,
        }

        query = (
            "SELECT admin.employee_menu__add("
            f"employee_id_ := {employee_id}, menu_id_ := {menu_id})"
        )
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.fetchval(
                "SELECT admin.employee_menu__add("
                "  employee_id_ := $1, menu_id_ := $2"
                ")",
                employee_id,
                menu_id,
            )
            if settings.TEST_MODE:
                await tr.rollback()
                logger.info("TEST_MODE: Rolled back employee_menu_add")
                query += " [TEST_MODE: ROLLED BACK]"
            else:
                await tr.commit()
        except Exception:
            await tr.rollback()
            raise

    logger.info(
        "employee_menu__add OK  employee_id=%s menu_id=%s",
        employee_id,
        menu_id,
    )
    return {"sql": query, **entity_info}


# ═══════════════════════════════════════════════════════════════════════════
# ACTION 3: Привязка модуля
#   PG-функция:  admin.employee_module_link(module_id_, employee_id_)
#   Поведение:   TOGGLE (INSERT if absent, DELETE if present)
#   Smart Assign: перед вызовом проверяем count, вызываем ТОЛЬКО если 0
# ═══════════════════════════════════════════════════════════════════════════


async def employee_module_link(employee_id: int, module_id: int) -> dict[str, Any]:
    """
    Дать сотруднику доступ к модулю (CRM, Склад, Офис…).

    ⚠ PG-функция — Toggle: повторный вызов ОТКЛЮЧИТ модуль.
    Smart Assign: проверяем существование перед вызовом.
    Returns: Dict with Executed SQL statement and entity info.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        # Fetch name for logging
        module_name = await conn.fetchval(
            "SELECT module_name FROM admin.module_tab WHERE module_id = $1",
            module_id,
        )
        entity_info = {
            "entity_name": module_name or f"Unknown Module {module_id}",
            "entity_id": module_id,
        }

        exists = await conn.fetchval(
            "SELECT count(1) FROM admin.employee_module_tab "
            "WHERE employee_id = $1 AND module_id = $2",
            employee_id,
            module_id,
        )
        if exists:
            msg = f"-- SKIPPED (already assigned) employee_id={employee_id} module_id={module_id}"
            logger.warning("employee_module_link %s", msg)
            return {"sql": msg, **entity_info}

        query = (
            "SELECT admin.employee_module_link("
            f"module_id_ := {module_id}, employee_id_ := {employee_id})"
        )
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.fetchval(
                "SELECT admin.employee_module_link("
                "  module_id_ := $1, employee_id_ := $2"
                ")",
                module_id,
                employee_id,
            )
            if settings.TEST_MODE:
                await tr.rollback()
                logger.info("TEST_MODE: Rolled back employee_module_link")
                query += " [TEST_MODE: ROLLED BACK]"
            else:
                await tr.commit()
        except Exception:
            await tr.rollback()
            raise

    logger.info(
        "employee_module_link OK  employee_id=%s module_id=%s",
        employee_id,
        module_id,
    )
    return {"sql": query, **entity_info}


# ═══════════════════════════════════════════════════════════════════════════
# ACTION 4: Выдача гранта (точечного права)
#   PG-функция:  НЕ СУЩЕСТВУЕТ — делаем прямой INSERT
#   Поведение:   plain INSERT (безопасное, НЕ toggle)
# ═══════════════════════════════════════════════════════════════════════════


async def employee_grant_add(employee_id: int, grant_id: int) -> dict[str, Any]:
    """
    Выдать сотруднику точечное право (grant).

    ⚠ PG-функции employee_grant_add НЕТ в базе — прямой INSERT.
    ON CONFLICT DO NOTHING для идемпотентности.
    Returns: Dict with Executed SQL statement and entity info.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        # Fetch name for logging (with module context)
        row = await conn.fetchrow(
            """
            SELECT g.grant_name, m.module_name 
            FROM admin.grant_tab g
            LEFT JOIN admin.module_tab m ON g.module_id = m.module_id
            WHERE g.grant_id = $1
            """,
            grant_id,
        )
        if row:
            grant_name = row["grant_name"]
            module_name = row["module_name"] or "Unknown Module"
            entity_name = f"{grant_name} ({module_name})"
        else:
            entity_name = f"Unknown Grant {grant_id}"

        entity_info = {
            "entity_name": entity_name,
            "entity_id": grant_id,
        }

        query = (
            "INSERT INTO admin.employee_grant_tab (employee_id, grant_id) "
            f"VALUES ({employee_id}, {grant_id}) ON CONFLICT DO NOTHING"
        )

        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                "INSERT INTO admin.employee_grant_tab (employee_id, grant_id) "
                "VALUES ($1, $2) "
                "ON CONFLICT DO NOTHING",
                employee_id,
                grant_id,
            )
            if settings.TEST_MODE:
                await tr.rollback()
                logger.info("TEST_MODE: Rolled back employee_grant_add")
                query += " [TEST_MODE: ROLLED BACK]"
            else:
                await tr.commit()
        except Exception:
            await tr.rollback()
            raise

    logger.info(
        "employee_grant_add OK  employee_id=%s grant_id=%s",
        employee_id,
        grant_id,
    )
    return {"sql": query, **entity_info}


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
    _log_db_response("get_menu_by_url", {"url": url, "result": result})
    return result


# ── Display names for preview (entity_id → human-readable name) ─────────────


async def get_menu_display_name(menu_id: int) -> str:
    """Название меню (без модуля в скобках) для отображения в превью."""
    pool = get_pool()
    async with pool.acquire() as conn:
        name = await conn.fetchval(
            "SELECT menu_name FROM admin.menu_tab WHERE menu_id = $1",
            menu_id,
        )
    return name or f"Меню #{menu_id}"


async def get_grant_display_name(grant_id: int) -> str:
    """Название права для отображения в превью."""
    pool = get_pool()
    async with pool.acquire() as conn:
        name = await conn.fetchval(
            "SELECT grant_name FROM admin.grant_tab WHERE grant_id = $1",
            grant_id,
        )
    return name or f"Право #{grant_id}"


async def get_group_display_name(group_id: int) -> str:
    """Название роли/группы для отображения в превью."""
    pool = get_pool()
    async with pool.acquire() as conn:
        name = await conn.fetchval(
            "SELECT group_name FROM admin.group_tab WHERE group_id = $1",
            group_id,
        )
    return name or f"Роль #{group_id}"


async def get_module_display_name(module_id: int) -> str:
    """Название модуля для отображения в превью."""
    pool = get_pool()
    async with pool.acquire() as conn:
        name = await conn.fetchval(
            "SELECT module_name FROM admin.module_tab WHERE module_id = $1",
            module_id,
        )
    return name or f"Модуль #{module_id}"


async def get_employee_context(employee_id: int) -> str:
    """
    Return a short textual context with real employee identity data from DB.

    Использует агрегаты по модулям и группам, чтобы LLM видел,
    в каких модулях и ролях пользователь уже состоит.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT 
                e.fio,
                p.position_name,
                e.email,
                -- Текущие модули сотрудника
                (
                    SELECT string_agg(m.module_name, ', ')
                    FROM admin.employee_module_tab em
                    JOIN admin.module_tab m ON em.module_id = m.module_id
                    WHERE em.employee_id = e.employee_id
                ) AS current_modules,
                -- Текущие группы (роли) сотрудника
                (
                    SELECT string_agg(g.group_name, ', ')
                    FROM admin.employee_group_tab eg
                    JOIN admin.group_tab g ON eg.group_id = g.group_id
                    WHERE eg.employee_id = e.employee_id
                ) AS current_groups
            FROM admin.employee_tab e
            LEFT JOIN admin.position_tab p ON e.position_id = p.position_id
            WHERE e.employee_id = $1
            """,
            employee_id,
        )

    if not row:
        logger.warning("get_employee_context employee_id=%s not found", employee_id)
        return "Сотрудник не найден."

    fio = row["fio"] or "Не указано"
    position_name = row["position_name"] or "Не указана"
    email = (row["email"] or "").strip() or "Не указан"
    current_modules = (row["current_modules"] or "").strip() or "нет модулей"
    current_groups = (row["current_groups"] or "").strip() or "нет групп"

    context = (
        f"ФИО: {fio}; "
        f"Должность: {position_name}; "
        f"Email: {email}; "
        f"Текущие модули: {current_modules}; "
        f"Текущие группы: {current_groups}"
    )
    logger.info(
        "get_employee_context employee_id=%s position=%r", employee_id, position_name
    )
    _log_db_response(
        "get_employee_context",
        {
            "fio": fio,
            "position_name": position_name,
            "email": email,
            "current_modules": current_modules,
            "current_groups": current_groups,
        },
    )
    return context


# ═══════════════════════════════════════════════════════════════════════════
# FETCH: все модули из admin.module_tab
# ═══════════════════════════════════════════════════════════════════════════


async def get_all_modules() -> list[ModuleDto]:
    """
    Return all modules from admin.module_tab.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM admin.module_tab")
    result = [ModuleDto.model_validate(dict(r)) for r in rows]
    _log_db_response("get_all_modules", result)
    return result


async def get_modules_for_employee(employee_id: int) -> list[ModuleDto]:
    """
    Return only modules that are linked to the given employee (employee_module_tab).
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT m.*
            FROM admin.module_tab m
            JOIN admin.employee_module_tab em
              ON em.module_id = m.module_id
            WHERE em.employee_id = $1
            ORDER BY m.module_id
            """,
            employee_id,
        )
    result = [ModuleDto.model_validate(dict(r)) for r in rows]
    _log_db_response("get_modules_for_employee", result)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# FETCH: должности из admin.position_tab и города из admin.city_tab
# ═══════════════════════════════════════════════════════════════════════════


async def get_all_positions() -> list[PositionDto]:
    """
    Возвращает все активные должности из admin.position_tab (is_active = true).
    """
    pool = get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT position_id, position_name, position_code, is_smart, is_active, module_id
                FROM admin.position_tab
                WHERE (is_active = true OR is_active::text = 'True')
                ORDER BY position_id
                """
            )
        result = [PositionDto.model_validate(dict(r)) for r in rows]
        _log_db_response("get_all_positions", result)
        return result
    except Exception as e:
        logger.warning("get_all_positions failed: %s", e)
        return []


async def get_all_cities() -> list[CityDto]:
    """
    Возвращает города из admin.city_tab (для привязки к пользователю).
    """
    pool = get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT city_id, city_name
                FROM admin.city_tab
                ORDER BY city_id
                """
            )
        result = [CityDto.model_validate(dict(r)) for r in rows]
        _log_db_response("get_all_cities", result)
        return result
    except Exception as e:
        logger.warning(
            "get_all_cities failed (проверьте наличие admin.city_tab и колонки city_name): %s",
            e,
        )
        return []


async def get_city_name(city_id: int) -> str | None:
    """Возвращает название города по city_id для превью."""
    pool = get_pool()
    try:
        async with pool.acquire() as conn:
            name = await conn.fetchval(
                "SELECT city_name FROM admin.city_tab WHERE city_id = $1",
                city_id,
            )
            return (name or "").strip() or None
    except Exception as e:
        logger.warning("get_city_name failed for city_id=%s: %s", city_id, e)
        return None


# ── FETCH: меню и права по модулю (для ручного выбора в заявке) ─────────────


async def get_menus_by_module(module_id: int) -> list[dict[str, Any]]:
    """
    Список меню модуля для выбора в заявке (id, название, parent для дерева).
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT menu_id, menu_name, menu_pid
            FROM admin.menu_tab
            WHERE module_id = $1
            ORDER BY menu_name
            """,
            module_id,
        )
    return [
        {
            "menu_id": r["menu_id"],
            "menu_name": r["menu_name"],
            "menu_pid": r["menu_pid"],
        }
        for r in rows
    ]


# ═══════════════════════════════════════════════════════════════════════════
# FETCH: все права из admin.grant_tab
# ═══════════════════════════════════════════════════════════════════════════


async def get_grants_by_module(module_id: int) -> list[dict[str, Any]]:
    """
    Список прав модуля для выбора в заявке (id, название, parent для дерева).
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT grant_id, grant_name, grant_pid
            FROM admin.grant_tab
            WHERE module_id = $1
            ORDER BY grant_name
            """,
            module_id,
        )
    return [
        {
            "grant_id": r["grant_id"],
            "grant_name": r["grant_name"],
            "grant_pid": r["grant_pid"],
        }
        for r in rows
    ]


async def get_all_grants_by_module(module_id: int) -> list[GrantDto]:
    """
    Return all grants from admin.grant_tab by module_id.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM admin.grant_tab WHERE module_id = $1", module_id
        )
    result = [GrantDto.model_validate(dict(r)) for r in rows]
    _log_db_response("get_all_grants_by_module", result)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# SEARCH: поиск пользователей по ФИО (similarity)
# ═══════════════════════════════════════════════════════════════════════════


async def search_employees_for_autocomplete(
    query: str, limit: int = 10
) -> list[EmployeeSearchItem]:
    """
    Поиск сотрудников по подстроке ФИО (pg_trgm similarity).
    Для автокомплита «Копируем права от»: топ N по similarity.
    """
    q = (query or "").strip()
    if not q or len(q) < 2:
        return []
    pool = get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT e.employee_id, e.fio, e.email
                FROM admin.employee_tab e
                WHERE e.fio % $1::text
                   OR e.fio ILIKE $2
                ORDER BY similarity(e.fio, $1::text) DESC NULLS LAST
                LIMIT $3
                """,
                q,
                f"%{q}%",
                limit,
            )
        result = [EmployeeSearchItem.model_validate(dict(r)) for r in rows]
        _log_db_response("search_employees_for_autocomplete", result)
        return result
    except Exception as e:
        logger.warning("search_employees_for_autocomplete failed: %s", e)
        return []


async def search_users_by_fios(fios: list[str]) -> list[EmployeeDto]:
    """
    Search users by FIOs (pg_trgm similarity). Tolerates typos / minor mistakes.
    Returns same shape as get_employee_context: employee_id, fio, position_name, module_name.
    """
    if not fios:
        return []
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (e.employee_id)
                e.employee_id,
                e.fio,
                p.position_name,
                m.module_name
            FROM admin.employee_tab e
            LEFT JOIN admin.position_tab p ON e.position_id = p.position_id
            LEFT JOIN admin.module_tab m ON p.module_id = m.module_id
            WHERE EXISTS (
                SELECT 1 FROM unnest($1::text[]) AS u(f)
                WHERE e.fio % u.f
            )
            ORDER BY e.employee_id
            """,
            fios,
        )
    result = [EmployeeDto.model_validate(dict(r)) for r in rows]
    _log_db_response("search_users_by_fios", result)
    return result


async def check_email_exists(email: str) -> bool:
    """
    Проверяет, занят ли email в admin.employee_tab.
    Возвращает True, если запись с таким email уже существует.
    """
    email_trimmed = email.strip().lower() if email else ""
    if not email_trimmed:
        return False
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchval(
            "SELECT 1 FROM admin.employee_tab WHERE email = trim(lower($1)) LIMIT 1",
            email,
        )
    return row is not None


async def get_employee_id_by_email(email: str) -> int | None:
    """
    Возвращает employee_id по email из admin.employee_tab.
    Для клонирования: «создай как у пользователя с email X».
    """
    email_trimmed = (email or "").strip().lower()
    if not email_trimmed:
        return None
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchval(
            "SELECT employee_id FROM admin.employee_tab WHERE email = trim(lower($1)) LIMIT 1",
            email,
        )
    return int(row) if row is not None else None


async def get_employee_display_info(employee_id: int) -> dict[str, Any] | None:
    """
    Возвращает ФИО и email сотрудника для превью (от кого клонируем).
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT employee_id, fio, email FROM admin.employee_tab WHERE employee_id = $1",
            employee_id,
        )
    result = dict(row) if row else None
    _log_db_response("get_employee_display_info", result)
    return result


async def get_company_id(employee_id: int) -> int | None:
    """
    Возвращает company_id для сотрудника (хранимая процедура get_company_id).
    Если функция в БД отсутствует или возвращает NULL — возвращаем None.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                "SELECT get_company_id($1) AS company_id",
                employee_id,
            )
            if row and row["company_id"] is not None:
                company_id = int(row["company_id"])
                _log_db_response(
                    "get_company_id",
                    {"employee_id": employee_id, "company_id": company_id},
                )
                return company_id
        except asyncpg.UndefinedFunctionError:
            logger.debug("get_company_id not found in DB, employee_id=%s", employee_id)
        except Exception as e:
            logger.warning("get_company_id failed employee_id=%s: %s", employee_id, e)
    _log_db_response("get_company_id", {"employee_id": employee_id, "company_id": None})
    return None


async def get_companies() -> list[dict[str, Any]]:
    """
    Список компаний для резолва по имени (admin.company_tab).
    Если таблица отсутствует — возвращает [].
    """
    pool = get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT company_id, company_name FROM admin.company_tab ORDER BY company_id"
            )
        result = [dict(r) for r in rows]
        _log_db_response("get_companies", result)
        return result
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.debug("get_companies skipped (table or column missing): %s", e)
        return []


# ═══════════════════════════════════════════════════════════════════════════
# SQL approval requests: заявки на исполнение SQL для аппрува админом
# ═══════════════════════════════════════════════════════════════════════════


async def create_sql_approval_request(
    *,
    request_type: str,
    sql_queries: list[Any],
    user_prompt: str | None = None,
    business_reason: str | None = None,
    created_by: int | None = None,
) -> int:
    """
    Создаёт запись в ai_admin.sql_approval_requests_tab.
    sql_queries: для grants/user_creation — список SQL-строк; для access_request — список {tool, args}.
    Сохраняет то, что написал пользователь (user_prompt, business_reason), чтобы админ
    мог прочитать и решить — давать ли доступ / выполнять ли SQL.

    Возвращает request_id (PK новой записи).
    """
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO ai_admin.sql_approval_requests_tab
        (request_type, user_prompt, business_reason, sql_queries, status, created_by)
        VALUES ($1, $2, $3, $4::jsonb, 'pending', $5)
        RETURNING request_id
        """,
        request_type,
        (user_prompt or "").strip() or None,
        (business_reason or "").strip() or None,
        json.dumps(sql_queries, ensure_ascii=False),
        created_by,
    )
    request_id = int(row["request_id"])
    logger.info(
        "create_sql_approval_request request_type=%s request_id=%s created_by=%s",
        request_type,
        request_id,
        created_by,
    )
    return request_id


async def list_sql_approval_requests(
    *,
    request_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Вернуть последние заявки из ai_admin.sql_approval_requests_tab
    (для админской страницы обработки запросов).
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                r.request_id,
                r.request_type,
                r.user_prompt,
                r.business_reason,
                r.sql_queries,
                r.status,
                r.created_by,
                r.created_at,
                e.fio AS created_by_fio
            FROM ai_admin.sql_approval_requests_tab r
            LEFT JOIN admin.employee_tab e ON e.employee_id = r.created_by
            WHERE ($1::text IS NULL OR r.request_type = $1::text)
              AND ($2::text IS NULL OR r.status = $2::text)
            ORDER BY r.created_at DESC
            LIMIT $3
            """,
            request_type,
            status,
            limit,
        )
        result = [dict(row) for row in rows]

        # Для access_request разворачиваем sql_queries в человекочитаемые названия меню/прав
        menu_ids_global: set[int] = set()
        grant_ids_global: set[int] = set()

        for row in result:
            if row.get("request_type") != "access_request":
                continue
            q = row.get("sql_queries")
            try:
                actions = json.loads(q) if isinstance(q, str) else (q or [])
            except Exception:
                actions = []
            menu_ids: set[int] = set()
            grant_ids: set[int] = set()
            for action in actions:
                if not isinstance(action, dict):
                    continue
                tool = action.get("tool")
                args = action.get("args") or {}
                if tool == "add_interface_button" and "menu_id" in args:
                    mid = int(args["menu_id"])
                    menu_ids.add(mid)
                    menu_ids_global.add(mid)
                elif tool == "add_grant" and "grant_id" in args:
                    gid = int(args["grant_id"])
                    grant_ids.add(gid)
                    grant_ids_global.add(gid)
            row["_menu_ids"] = menu_ids
            row["_grant_ids"] = grant_ids

        menu_name_map: dict[int, str] = {}
        grant_name_map: dict[int, str] = {}

        if menu_ids_global:
            mrows = await conn.fetch(
                """
                SELECT t.menu_id, t.menu_name, m.module_name
                FROM admin.menu_tab t
                LEFT JOIN admin.module_tab m ON t.module_id = m.module_id
                WHERE t.menu_id = ANY($1::int[])
                """,
                list(menu_ids_global),
            )
            for mrow in mrows:
                module_name = mrow["module_name"]
                base = mrow["menu_name"]
                menu_name_map[mrow["menu_id"]] = (
                    f"{base} ({module_name})" if module_name else base
                )

        if grant_ids_global:
            grows = await conn.fetch(
                """
                SELECT g.grant_id, g.grant_name, m.module_name
                FROM admin.grant_tab g
                LEFT JOIN admin.module_tab m ON g.module_id = m.module_id
                WHERE g.grant_id = ANY($1::int[])
                """,
                list(grant_ids_global),
            )
            for grow in grows:
                module_name = grow["module_name"]
                base = grow["grant_name"]
                grant_name_map[grow["grant_id"]] = (
                    f"{base} ({module_name})" if module_name else base
                )

        for row in result:
            mids = row.pop("_menu_ids", set())
            gids = row.pop("_grant_ids", set())
            if mids:
                row["menu_names"] = [
                    menu_name_map.get(mid, f"menu_id={mid}") for mid in sorted(mids)
                ]
            if gids:
                row["grant_names"] = [
                    grant_name_map.get(gid, f"grant_id={gid}") for gid in sorted(gids)
                ]

    _log_db_response("list_sql_approval_requests", result)
    return result


async def get_sql_approval_request(request_id: int) -> dict[str, Any] | None:
    """Получить одну заявку по request_id."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                r.request_id,
                r.request_type,
                r.user_prompt,
                r.business_reason,
                r.sql_queries,
                r.status,
                r.created_by,
                r.created_at,
                e.fio AS created_by_fio
            FROM ai_admin.sql_approval_requests_tab r
            LEFT JOIN admin.employee_tab e ON e.employee_id = r.created_by
            WHERE r.request_id = $1
            """,
            request_id,
        )
    return dict(row) if row else None


async def update_sql_approval_request_status(
    *,
    request_id: int,
    status: str,
    approved_by: int | None = None,
    comment: str | None = None,
) -> None:
    """Обновить статус заявки (approve / reject / executed / failed)."""
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE ai_admin.sql_approval_requests_tab
            SET status = $2,
                approved_by = COALESCE($3, approved_by),
                approved_at = CASE WHEN $3 IS NOT NULL THEN now() ELSE approved_at END,
                comment = COALESCE($4, comment)
            WHERE request_id = $1
            """,
            request_id,
            status,
            approved_by,
            comment,
        )


# ═══════════════════════════════════════════════════════════════════════════
# AUDIT: логирование AI-запросов
# ═══════════════════════════════════════════════════════════════════════════


async def log_ai_request(
    *,
    user_id: int,
    prompt: str,
    reason: str,
    ai_decision: list[dict[str, Any]],
    ai_message: str | None,
    execution_status: str,
) -> None:
    """
    Log an AI interaction event to the database.
    Fire-and-forget style (catches exceptions to avoid breaking the main flow).
    """
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO ai_admin.audit_logs_tab "
                "(employee_id, user_prompt, business_reason, ai_decision, ai_message, execution_status) "
                "VALUES ($1, $2, $3, $4::jsonb, $5, $6)",
                user_id,
                prompt,
                reason,
                json.dumps(ai_decision, ensure_ascii=False),
                ai_message,
                execution_status,
            )
        logger.info(
            "log_ai_request saved for user_id=%s status=%s", user_id, execution_status
        )
    except Exception as e:
        logger.error("Failed to save audit log: %s", e)


async def get_recent_audit_logs(limit: int = 10) -> list[dict[str, Any]]:
    """
    Return most recent audit log records (default 10).
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT log_id,
                   employee_id,
                   user_prompt,
                   business_reason,
                   ai_decision,
                   ai_message,
                   execution_status,
                   created_at
            FROM ai_admin.audit_logs_tab
            ORDER BY created_at DESC, log_id DESC
            LIMIT $1
            """,
            limit,
        )
    results: list[dict[str, Any]] = []
    for r in rows:
        item = dict(r)
        # asyncpg may return jsonb as a string depending on codec setup
        if isinstance(item.get("ai_decision"), str):
            try:
                item["ai_decision"] = json.loads(item["ai_decision"])
            except Exception:
                item["ai_decision"] = []
        results.append(item)
    _log_db_response("get_recent_audit_logs", results)
    return results


async def check_daily_limit(employee_id: int) -> dict[str, Any]:
    """
    Check if the user has exceeded their daily AI request limit.
    Calls PostgreSQL function: ai_admin.check_daily_limit(employee_id)
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM ai_admin.check_daily_limit($1)",
            employee_id,
        )
    result = dict(row) if row else {}
    _log_db_response("check_daily_limit", result)
    return result
