"""
Database service — stub implementations.

Every function logs the call via the standard logger instead of touching a
real database.  To switch to production, replace the stub bodies with actual
asyncpg calls (production SQL is documented in each docstring).

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

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# FETCH: текущие права сотрудника
# ═══════════════════════════════════════════════════════════════════════════


async def get_user_permissions(employee_id: int) -> dict[str, list[int]]:
    """
    Return every permission the employee currently has.

    Production SQL (4 queries):
        SELECT group_id  FROM admin.employee_group_tab  WHERE employee_id = $1;
        SELECT menu_id   FROM admin.employee_menu_tab   WHERE employee_id = $1;
        SELECT module_id FROM admin.employee_module_tab  WHERE employee_id = $1;
        SELECT grant_id  FROM admin.employee_grant_tab   WHERE employee_id = $1;
    """
    logger.info("[STUB] get_user_permissions  employee_id=%s", employee_id)

    # Stub: return empty lists (no existing permissions)
    return {
        "group_ids": [],
        "menu_ids": [],
        "module_ids": [],
        "grant_ids": [],
    }


# ═══════════════════════════════════════════════════════════════════════════
# ACTION 1: Назначение группы / роли
#   PG-функция:  admin.employee_group_link(group_id_, employee_id_)
#   Поведение:   TOGGLE (INSERT if absent, DELETE if present)
#   Проверка:    group_tab.is_active = true
#   Smart Assign: перед вызовом проверяем count, вызываем ТОЛЬКО если 0
# ═══════════════════════════════════════════════════════════════════════════


async def employee_group_link(employee_id: int, group_id: int) -> None:
    """
    Назначить сотруднику группу (роль).

    ⚠ PG-функция — Toggle: повторный вызов УДАЛИТ роль.
    В production обязательна проверка перед вызовом.

    Production SQL (Smart Assign):
        -- 1) check existence
        SELECT count(1) FROM admin.employee_group_tab
         WHERE employee_id = $1 AND group_id = $2;
        -- 2) ONLY if count = 0:
        SELECT admin.employee_group_link(
            group_id_ := $2, employee_id_ := $1
        );
    """
    logger.info(
        "[STUB] admin.employee_group_link(group_id_=%s, employee_id_=%s)",
        group_id,
        employee_id,
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

    Production SQL:
        SELECT admin.employee_menu__add(
            employee_id_ := $1, menu_id_ := $2
        );
    """
    logger.info(
        "[STUB] admin.employee_menu__add(employee_id_=%s, menu_id_=%s)",
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
    В production обязательна проверка перед вызовом.

    Production SQL (Smart Assign):
        -- 1) check existence
        SELECT count(1) FROM admin.employee_module_tab
         WHERE employee_id = $1 AND module_id = $2;
        -- 2) ONLY if count = 0:
        SELECT admin.employee_module_link(
            module_id_ := $2, employee_id_ := $1
        );
    """
    logger.info(
        "[STUB] admin.employee_module_link(module_id_=%s, employee_id_=%s)",
        module_id,
        employee_id,
    )


# ═══════════════════════════════════════════════════════════════════════════
# ACTION 4: Выдача гранта (точечного права)
#   PG-функция:  НЕ СУЩЕСТВУЕТ — делаем прямой INSERT
#   Поведение:   plain INSERT (безопасное, НЕ toggle)
# ═══════════════════════════════════════════════════════════════════════════


async def employee_grant_add(employee_id: int, grant_id: int) -> None:
    """
    Выдать сотруднику точечное право (grant).

    ⚠ PG-функции employee_grant_add НЕТ в базе.
    Production делает прямой INSERT.

    Production SQL:
        INSERT INTO admin.employee_grant_tab (employee_id, grant_id)
        VALUES ($1, $2)
        ON CONFLICT DO NOTHING;
    """
    logger.info(
        "[STUB] INSERT admin.employee_grant_tab(employee_id=%s, grant_id=%s)",
        employee_id,
        grant_id,
    )


# ═══════════════════════════════════════════════════════════════════════════
# CONTEXT: поиск menu_id по URL
#   PG-функция:  admin.get_menu_by_url(url_)
#   Возвращает:  integer (menu_id)
# ═══════════════════════════════════════════════════════════════════════════


async def get_menu_by_url(url: str) -> int | None:
    """
    Найти menu_id по URL-адресу страницы интерфейса.

    Production SQL:
        SELECT admin.get_menu_by_url(url_ := $1);
    """
    logger.info("[STUB] admin.get_menu_by_url(url_=%r)", url)

    # Stub: return None (not found)
    return None


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
    """
    Persist an AI interaction record.

    Production SQL:
        INSERT INTO admin.ai_request_logs
            (user_id, prompt, reason, tool_name, tool_args, result_summary)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6);
    """
    logger.info(
        "[STUB] log_ai_request  user_id=%s  tool=%s  status=%s  reason=%r  args=%s",
        user_id,
        tool_name,
        result_summary,
        reason[:80],
        tool_args,
    )
