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
from app.api.schemas import EmployeeDto, GrantDto, ModuleDto
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
    return result


async def get_employee_context(employee_id: int) -> str:
    """
    Return a short textual context with real employee identity data from DB.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                e.fio,
                p.position_name,
                m.module_name
            FROM admin.employee_tab e
            LEFT JOIN admin.position_tab p ON e.position_id = p.position_id
            LEFT JOIN admin.module_tab m ON p.module_id = m.module_id
            WHERE e.employee_id = $1
            """,
            employee_id,
        )

    if not row:
        logger.warning("get_employee_context employee_id=%s not found", employee_id)
        return "Сотрудник не найден."

    fio = row["fio"] or "Не указано"
    position_name = row["position_name"] or "Не указана"
    module_name = row["module_name"] or "Не указан"
    context = f"ФИО: {fio}, Должность: {position_name}, Модуль: {module_name}"
    logger.info(
        "get_employee_context employee_id=%s position=%r", employee_id, position_name
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
    return [ModuleDto.model_validate(dict(r)) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════
# FETCH: все права из admin.grant_tab
# ═══════════════════════════════════════════════════════════════════════════


async def get_all_grants_by_module(module_id: int) -> list[GrantDto]:
    """
    Return all grants from admin.grant_tab by module_id.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM admin.grant_tab WHERE module_id = $1", module_id
        )
    return [GrantDto.model_validate(dict(r)) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════
# SEARCH: поиск пользователей по ФИО (similarity)
# ═══════════════════════════════════════════════════════════════════════════


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
    return [EmployeeDto.model_validate(dict(r)) for r in rows]


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
    return dict(row) if row else None


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
                return int(row["company_id"])
        except asyncpg.UndefinedFunctionError:
            logger.debug("get_company_id not found in DB, employee_id=%s", employee_id)
        except Exception as e:
            logger.warning("get_company_id failed employee_id=%s: %s", employee_id, e)
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
        return [dict(r) for r in rows]
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.debug("get_companies skipped (table or column missing): %s", e)
        return []


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
    return dict(row) if row else {}
