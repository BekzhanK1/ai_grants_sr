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
# SEARCH: поиск по справочникам (меню, группы, гранты)
# ═══════════════════════════════════════════════════════════════════════════


async def search_menu(query: str) -> list[dict[str, Any]]:
    """
    Поиск пунктов меню по названию.
    Только активные. Включает parent_name и module_name.
    Нечёткий поиск (pg_trgm), если доступен; иначе ILIKE.
    """
    query = query.strip()
    if not query:
        return []
    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                "SELECT t.menu_id, t.menu_name, t.menu_action, "
                "       p.menu_name AS parent_name, m.module_name "
                "FROM admin.menu_tab t "
                "LEFT JOIN admin.menu_tab p ON t.menu_pid = p.menu_id "
                "LEFT JOIN admin.module_tab m ON t.module_id = m.module_id "
                "WHERE t.is_active = 1 "
                "  AND (t.menu_name ILIKE '%' || $1 || '%' OR t.menu_name % $1) "
                "ORDER BY similarity(t.menu_name, $1) DESC NULLS LAST, t.menu_id "
                "LIMIT 15",
                query,
            )
        except asyncpg.UndefinedFunctionError:
            rows = await conn.fetch(
                "SELECT t.menu_id, t.menu_name, t.menu_action, "
                "       p.menu_name AS parent_name, m.module_name "
                "FROM admin.menu_tab t "
                "LEFT JOIN admin.menu_tab p ON t.menu_pid = p.menu_id "
                "LEFT JOIN admin.module_tab m ON t.module_id = m.module_id "
                "WHERE t.is_active = 1 AND t.menu_name ILIKE '%' || $1 || '%' "
                "ORDER BY t.menu_id LIMIT 15",
                query,
            )
    results = [dict(r) for r in rows]
    logger.info("search_menu  query=%r  found=%d", query, len(results))
    return results


async def search_group(query: str) -> list[dict[str, Any]]:
    """
    Поиск групп (ролей) по названию.
    Только активные. Включает module_name для контекста.
    Использует нечёткий поиск (pg_trgm similarity), если расширение включено —
    так находятся варианты вроде «Менеджер call-centra» по запросу «менеджера call-центра».
    Fallback на ILIKE, если pg_trgm не установлен.
    """
    query = query.strip()
    if not query:
        return []
    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                "SELECT g.group_id, g.group_name, g.group_code, m.module_name "
                "FROM admin.group_tab g "
                "LEFT JOIN admin.module_tab m ON g.module_id = m.module_id "
                "WHERE g.is_active = true "
                "  AND (g.group_name ILIKE '%' || $1 || '%' OR g.group_name % $1) "
                "ORDER BY similarity(g.group_name, $1) DESC NULLS LAST, g.group_id "
                "LIMIT 15",
                query,
            )
        except asyncpg.UndefinedFunctionError:
            rows = await conn.fetch(
                "SELECT g.group_id, g.group_name, g.group_code, m.module_name "
                "FROM admin.group_tab g "
                "LEFT JOIN admin.module_tab m ON g.module_id = m.module_id "
                "WHERE g.is_active = true "
                "  AND g.group_name ILIKE '%' || $1 || '%' "
                "ORDER BY g.group_id LIMIT 15",
                query,
            )
    results = [dict(r) for r in rows]
    logger.info("search_group  query=%r  found=%d", query, len(results))
    return results


async def search_grant(query: str) -> list[dict[str, Any]]:
    """
    Поиск грантов по названию.
    Только активные. Включает parent_name и module_name.
    Нечёткий поиск (pg_trgm), если доступен; иначе ILIKE.
    """
    query = query.strip()
    if not query:
        return []
    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                "SELECT g.grant_id, g.grant_name, g.grant_code, "
                "       p.grant_name AS parent_name, m.module_name "
                "FROM admin.grant_tab g "
                "LEFT JOIN admin.grant_tab p ON g.grant_pid = p.grant_id "
                "LEFT JOIN admin.module_tab m ON g.module_id = m.module_id "
                "WHERE g.is_active = 1 "
                "  AND (g.grant_name ILIKE '%' || $1 || '%' OR g.grant_name % $1) "
                "ORDER BY similarity(g.grant_name, $1) DESC NULLS LAST, g.grant_id "
                "LIMIT 15",
                query,
            )
        except asyncpg.UndefinedFunctionError:
            rows = await conn.fetch(
                "SELECT g.grant_id, g.grant_name, g.grant_code, "
                "       p.grant_name AS parent_name, m.module_name "
                "FROM admin.grant_tab g "
                "LEFT JOIN admin.grant_tab p ON g.grant_pid = p.grant_id "
                "LEFT JOIN admin.module_tab m ON g.module_id = m.module_id "
                "WHERE g.is_active = 1 AND g.grant_name ILIKE '%' || $1 || '%' "
                "ORDER BY g.grant_id LIMIT 15",
                query,
            )
    results = [dict(r) for r in rows]
    logger.info("search_grant  query=%r  found=%d", query, len(results))
    return results


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
    ai_decision: list[dict[str, Any]],
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
                "INSERT INTO ai_admin.audit_logs "
                "(employee_id, user_prompt, business_reason, ai_decision, execution_status) "
                "VALUES ($1, $2, $3, $4::jsonb, $5)",
                user_id,
                prompt,
                reason,
                json.dumps(ai_decision, ensure_ascii=False),
                execution_status,
            )
        logger.info("log_ai_request saved for user_id=%s status=%s", user_id, execution_status)
    except Exception as e:
        logger.error("Failed to save audit log: %s", e)
