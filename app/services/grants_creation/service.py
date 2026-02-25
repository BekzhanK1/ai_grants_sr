"""
Grants creation pipeline: подготовка иерархии прав по дереву с отступами.

Вход: module_id, prompt_text (дерево "grant_code grant_name" по отступам, блок Сотрудники/Кому — ФИО).
Шаги: парсинг дерева в коде (existing/new, visual_tree), search_users_by_fios, get_company_id, один запрос к LLM только за SQL.
Выход: GrantsPreparationResult (new_grants, existing_grants_in_tree, users, sql_queries, visual_tree).
SQL не выполняется — только формирование структуры и скрипта.
"""

from __future__ import annotations

import json
import logging
import re

from app.api.schemas import (
    ConfirmGrantsResult,
    ExecuteGrantsResult,
    GrantDto,
    GrantsPreparationResult,
    ModuleDto,
    NewGrantItem,
    UserWithCompany,
)
from app.core.config import settings
from app.core.database import get_pool
from app.services.db_service import get_all_grants_by_module
from app.services.db_service import (
    get_all_modules as _get_all_modules_db,
)
from app.services.db_service import (
    create_sql_approval_request,
    get_company_id,
    get_modules_for_employee,
    search_users_by_fios,
)
from app.services.llm import create_chat_completion

logger = logging.getLogger(__name__)

# Заголовки секции пользователей (после них — ФИО, не дерево)
_USER_SECTION_HEADERS = ("сотрудники:", "users:", "пользователи:", "user:", "кому:")


def _extract_tree_section(prompt_text: str) -> str:
    """Возвращает часть текста до секции Сотрудники/Кому/Users (дерево прав)."""
    lines = prompt_text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if any(lower.startswith(h) for h in _USER_SECTION_HEADERS):
            return "\n".join(lines[:i]).strip()
    return prompt_text.strip()


def _parse_indent(line: str) -> int:
    """Количество ведущих пробелов или табов (таб = 4 для сравнения)."""
    width = 0
    for c in line:
        if c == "\t":
            width += 4
        elif c == " ":
            width += 1
        else:
            break
    return width


def parse_tree_to_grants(
    tree_text: str,
    existing_grants: list[GrantDto],
) -> tuple[list[GrantDto], list[NewGrantItem], str, list[str]]:
    """
    Парсит дерево по отступам. Иерархия по количеству отступов, строка = grant_code grant_name.
    Возвращает: (existing_grants_in_tree, new_grants, visual_tree, all_grant_codes).
    """
    existing_by_code = {g.grant_code: g for g in existing_grants}
    existing_seen: set[str] = set()
    new_seen: set[str] = set()
    existing_grants_in_tree: list[GrantDto] = []
    new_grants: list[NewGrantItem] = []
    visual_lines: list[str] = []
    all_codes_order: list[str] = []

    # Стек (indent, grant_code, grant_id | None) для определения родителя
    stack: list[tuple[int, str, int | None]] = []

    lines = tree_text.splitlines()
    for raw_line in lines:
        indent = _parse_indent(raw_line)
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith(_USER_SECTION_HEADERS):
            break
        # Убираем теги [NEW] / [EXISTING] — они только для визуализации
        line = re.sub(r"\s*\[(NEW|EXISTING)\]\s*$", "", line, flags=re.IGNORECASE)
        parts = line.split(None, 1)
        code = parts[0]
        name = parts[1] if len(parts) > 1 else code

        # Родитель — последняя строка с меньшим отступом
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent_indent, parent_code, parent_grant_id = (
            stack[-1] if stack else (-1, "", None)
        )

        if code in existing_by_code:
            g = existing_by_code[code]
            if code not in existing_seen:
                existing_seen.add(code)
                existing_grants_in_tree.append(g)
            all_codes_order.append(code)
            visual_lines.append(raw_line.rstrip() + " [EXISTING]")
            stack.append((indent, code, g.grant_id))
        else:
            grant_pid: int | None = parent_grant_id
            parent_grant_code: str | None = None
            if parent_grant_id is None and parent_code:
                parent_grant_code = parent_code
            if code not in new_seen:
                new_seen.add(code)
                new_grants.append(
                    NewGrantItem(
                        grant_code=code,
                        grant_name=name,
                        grant_pid=grant_pid,
                        parent_grant_code=parent_grant_code,
                        order_num=None,
                        is_can_be_parent=True,
                    )
                )
            all_codes_order.append(code)
            visual_lines.append(raw_line.rstrip() + " [NEW]")
            stack.append((indent, code, None))

    visual_tree = "\n".join(visual_lines)
    all_grant_codes = list(dict.fromkeys(all_codes_order))
    return existing_grants_in_tree, new_grants, visual_tree, all_grant_codes


def _extract_fios_from_tree_text(prompt_text: str) -> list[str]:
    """
    Извлекает потенциальные ФИО из текста дерева.
    Ищет блок после «Сотрудники:» / «Users:» / «Пользователи:» / «Кому:» / «User:» или строки, похожие на ФИО (кириллица, 2–4 слова).
    """
    lines = [line.strip() for line in prompt_text.splitlines() if line.strip()]
    fios: list[str] = []
    in_users_section = False
    for line in lines:
        lower = line.lower()
        if any(lower.startswith(h) for h in _USER_SECTION_HEADERS):
            in_users_section = True
            rest = line.split(":", 1)[-1].strip()
            if rest:
                # Поддержка перечисления через запятую: "Кому: Иванов Иван, Петров Петр"
                for part in rest.split(","):
                    part = part.strip()
                    if part:
                        fios.append(part)
            continue
        if in_users_section:
            if not line or line.startswith("#") or line.startswith("```"):
                in_users_section = False
                continue
            # убираем маркеры списка и дерева
            cleaned = re.sub(r"^[\s\-*├│└┤]+", "", line).strip()
            if (
                cleaned
                and 2 <= len(cleaned.split()) <= 5
                and re.search(r"[а-яА-ЯёЁ]", cleaned)
            ):
                fios.append(cleaned)

    if fios:
        return list(dict.fromkeys(fios))

    # Fallback: строки, похожие на ФИО (кириллица, без скобок «код (название)»)
    for line in lines:
        if "(" in line and ")" in line:
            continue
        cleaned = re.sub(r"^[\s\-*├│└┤]+", "", line).strip()
        if 2 <= len(cleaned.split()) <= 5 and re.search(r"[а-яА-ЯёЁ]", cleaned):
            fios.append(cleaned)
    return list(dict.fromkeys(fios))


def _rebuild_visual_tree(
    existing_grants_in_tree: list[GrantDto],
    new_grants: list[NewGrantItem],
) -> str:
    """
    Пересобирает visual_tree из подтверждённых данных (без парсинга дерева).
    Простой плоский список — existing [EXISTING], new [NEW].
    """
    lines: list[str] = []
    for g in existing_grants_in_tree:
        lines.append(f"{g.grant_code} {g.grant_name} [EXISTING]")
    for g in new_grants:
        lines.append(f"{g.grant_code} {g.grant_name} [NEW]")
    return "\n".join(lines)


def _build_sql_only_system_prompt(module_id: int) -> str:
    return f"""Ты — SQL Generator. На вход тебе даны структурированные списки: new_grants (новые гранты), existing_grants_in_tree (уже в БД), users (сотрудники с employee_id и company_id). Модуль: module_id={module_id}.

Правила построения SQL:

1. **Транзакционность**: Весь скрипт строго внутри BEGIN; ... COMMIT;

2. **INSERT в admin.grant_tab**:
   - Один INSERT с перечислением всех VALUES для строк из new_grants.
   - Если у элемента указан grant_pid (число) — подставляй его напрямую.
   - Если grant_pid равен null, но указан parent_grant_code — родитель тоже новый. Используй подзапрос: (SELECT grant_id FROM admin.grant_tab WHERE grant_code = 'parent_grant_code' LIMIT 1).
   - Колонки: grant_code, grant_name, grant_pid, module_id={module_id}, is_active=1, is_can_be_parent (true/false).

3. **INSERT в admin.employee_grant_tab**:
   - Оптимизированная конструкция: INSERT INTO admin.employee_grant_tab (employee_id, grant_id, company_id) SELECT ...
   - Временная таблица пользователей: FROM (VALUES (id1, cid1), (id2, cid2), ...) AS u(id, cid).
   - CROSS JOIN со списком всех grant_id по кодам из all_grant_codes: (SELECT grant_id FROM admin.grant_tab WHERE module_id = {module_id} AND grant_code IN (...)) AS g.
   - **ОБЯЗАТЕЛЬНО** вставляй company_id. В данных пользователей company_id уже подставлен (null заменён на 1). Используй значения как есть, НЕ используй NULL и COALESCE.

Выход — только JSON: {{ "sql_queries": ["один скрипт с \\n внутри"] }}. Возвращай ТОЛЬКО sql_queries, без visual_tree — его строит Python."""


def _build_sql_only_user_prompt(
    new_grants: list[NewGrantItem],
    existing_grants_in_tree: list[GrantDto],
    users: list[UserWithCompany],
    all_grant_codes: list[str],
) -> str:
    new_grants_json = json.dumps(
        [g.model_dump(mode="json") for g in new_grants],
        ensure_ascii=False,
        indent=2,
    )
    existing_json = json.dumps(
        [
            {
                "grant_id": g.grant_id,
                "grant_code": g.grant_code,
                "grant_name": g.grant_name,
            }
            for g in existing_grants_in_tree
        ],
        ensure_ascii=False,
        indent=2,
    )
    users_json = json.dumps(
        [
            {
                "employee_id": u.employee_id,
                "company_id": u.company_id if u.company_id is not None else 1,
                "fio": u.fio,
            }
            for u in users
        ],
        ensure_ascii=False,
        indent=2,
    )
    return f"""new_grants (вставлять в grant_tab одним INSERT):
{new_grants_json}

existing_grants_in_tree (уже в БД, для справки; в INSERT grant_tab не включать):
{existing_json}

users (для VALUES и CROSS JOIN; company_id уже подставлен, null заменён на 1 — используй как есть):
{users_json}

all_grant_codes (все коды из дерева для IN (...)):
{json.dumps(all_grant_codes, ensure_ascii=False)}

Верни только JSON: {{ "sql_queries": ["BEGIN;\\n...\\nCOMMIT;"] }}"""


async def get_all_modules(employee_id: int | None = None) -> list[ModuleDto]:
    """
    Возвращает модули для AI-интерфейса.

    Если передан employee_id — только модули, привязанные к сотруднику.
    Если None — все модули (fallback / сервисное использование).
    """
    if employee_id is not None:
        return await get_modules_for_employee(employee_id)
    return await _get_all_modules_db()


async def prepare_access_hierarchy(
    module_id: int,
    prompt_text: str,
) -> GrantsPreparationResult:
    """
    Подготавливает иерархию прав (Delta-only): парсит дерево в коде, LLM только генерирует SQL.
    Возвращает new_grants, existing_grants_in_tree, users, sql_queries, visual_tree.
    SQL не выполняется.
    """
    existing_grants = await get_all_grants_by_module(module_id)
    tree_section = _extract_tree_section(prompt_text)
    existing_grants_in_tree, new_grants, visual_tree, all_grant_codes = (
        parse_tree_to_grants(tree_section, existing_grants)
    )

    fios = _extract_fios_from_tree_text(prompt_text)
    employees = await search_users_by_fios(fios) if fios else []

    users: list[UserWithCompany] = []
    for emp in employees:
        company_id = await get_company_id(emp.employee_id)
        users.append(
            UserWithCompany(
                employee_id=emp.employee_id,
                company_id=company_id,
                fio=emp.fio,
                position_name=emp.position_name,
                module_name=emp.module_name,
            )
        )

    sql_queries: list[str] = []
    if new_grants or all_grant_codes:
        messages = [
            {"role": "system", "content": _build_sql_only_system_prompt(module_id)},
            {
                "role": "user",
                "content": _build_sql_only_user_prompt(
                    new_grants, existing_grants_in_tree, users, all_grant_codes
                ),
            },
        ]
        try:
            response = await create_chat_completion(
                messages,
                temperature=0.0,
                max_tokens=4096,
                tools=None,
            )
            content = (response.choices[0].message.content or "").strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*", "", content)
                content = re.sub(r"\s*```\s*$", "", content)
            data = json.loads(content)
            sql_queries = list(data.get("sql_queries", []))
        except json.JSONDecodeError as e:
            logger.warning("LLM SQL response is not valid JSON: %s", e)
        except Exception as e:
            logger.exception("LLM call failed: %s", e)

    # visual_tree строится только в Python, LLM не трогает
    no_company = [u.fio for u in users if u.company_id is None]
    if no_company:
        warning = (
            "⚠️ Внимание: для ["
            + "], [".join(no_company)
            + "] не найден company_id, доступ может быть не полным."
        )
        visual_tree = (
            (visual_tree + "\n\n" + warning).strip() if visual_tree else warning
        )

    return GrantsPreparationResult(
        new_grants=new_grants,
        existing_grants_in_tree=existing_grants_in_tree,
        users=users,
        sql_queries=sql_queries,
        visual_tree=visual_tree,
    )


# ═══════════════════════════════════════════════════════════════════════════
# ШАГ 2: Подтверждение — пользователь отредактировал данные → финальный SQL
# ═══════════════════════════════════════════════════════════════════════════


async def confirm_and_generate_sql(
    module_id: int,
    new_grants: list[NewGrantItem],
    existing_grants_in_tree: list[GrantDto],
    users: list[UserWithCompany],
) -> ConfirmGrantsResult:
    """
    Получает отредактированные данные от пользователя и генерирует финальный SQL через LLM.
    visual_tree и all_grant_codes пересчитываются из переданных данных.
    """
    # Пересчитать all_grant_codes из подтверждённых данных
    all_grant_codes = [g.grant_code for g in existing_grants_in_tree] + [
        g.grant_code for g in new_grants
    ]

    # Пересобрать visual_tree из подтверждённых данных
    visual_tree = _rebuild_visual_tree(existing_grants_in_tree, new_grants)

    messages = [
        {"role": "system", "content": _build_sql_only_system_prompt(module_id)},
        {
            "role": "user",
            "content": _build_sql_only_user_prompt(
                new_grants, existing_grants_in_tree, users, all_grant_codes
            ),
        },
    ]

    sql_queries: list[str] = []
    try:
        response = await create_chat_completion(
            messages,
            temperature=0.0,
            max_tokens=4096,
            tools=None,
        )
        content = (response.choices[0].message.content or "").strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```\s*$", "", content)
        data = json.loads(content)
        sql_queries = list(data.get("sql_queries", []))
    except json.JSONDecodeError as e:
        logger.warning("LLM SQL response is not valid JSON: %s", e)
    except Exception as e:
        logger.exception("LLM call failed: %s", e)

    no_company = [u.fio for u in users if u.company_id is None]
    if no_company:
        warning = (
            "⚠️ Внимание: для ["
            + "], [".join(no_company)
            + "] не найден company_id, доступ может быть не полным."
        )
        visual_tree = (
            (visual_tree + "\n\n" + warning).strip() if visual_tree else warning
        )

    return ConfirmGrantsResult(sql_queries=sql_queries, visual_tree=visual_tree)


# ═══════════════════════════════════════════════════════════════════════════
# ШАГ 3: Исполнение SQL
# ═══════════════════════════════════════════════════════════════════════════


async def execute_grants_sql(
    sql_queries: list[str],
    *,
    user_prompt: str | None = None,
    business_reason: str | None = None,
    created_by: int | None = None,
) -> ExecuteGrantsResult:
    """
    Выполняет финальные SQL-скрипты в одной транзакции.
    В TEST_MODE — откатывает транзакцию (ROLLBACK).

    При settings.ADMIN_APPROVE = True фактическое исполнение не происходит:
    заявка сохраняется в ai_admin.sql_approval_requests_tab (в т.ч. user_prompt и
    business_reason — то, что написал пользователь), возвращается request_id.
    """
    if settings.ADMIN_APPROVE:
        try:
            request_id = await create_sql_approval_request(
                request_type="grants",
                sql_queries=sql_queries,
                user_prompt=user_prompt,
                business_reason=business_reason,
                created_by=created_by,
            )
        except Exception as e:
            logger.exception("create_sql_approval_request failed: %s", e)
            return ExecuteGrantsResult(
                status="error",
                message=f"Не удалось сохранить заявку на аппрув: {e}",
                rows_affected=0,
                request_id=None,
            )
        return ExecuteGrantsResult(
            status="pending_approval",
            message=(
                "ADMIN_APPROVE включён: SQL не выполнен. "
                "Заявка сохранена. Админ может прочитать запрос пользователя и утвердить или отклонить."
            ),
            rows_affected=0,
            request_id=request_id,
        )

    pool = get_pool()
    total_rows = 0
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                for sql in sql_queries:
                    # Убираем BEGIN/COMMIT — мы уже внутри транзакции asyncpg
                    clean = sql.strip()
                    clean = re.sub(r"^\s*BEGIN\s*;\s*", "", clean, flags=re.IGNORECASE)
                    clean = re.sub(r"\s*COMMIT\s*;\s*$", "", clean, flags=re.IGNORECASE)
                    for statement in _split_statements(clean):
                        stmt = statement.strip()
                        if not stmt:
                            continue
                        result = await conn.execute(stmt)
                        # asyncpg returns "INSERT 0 N" or "UPDATE N" etc.
                        if result:
                            parts = result.split()
                            if parts and parts[-1].isdigit():
                                total_rows += int(parts[-1])

                if settings.TEST_MODE:
                    raise _RollbackSignal()

        return ExecuteGrantsResult(
            status="success",
            message=f"SQL выполнен. Затронуто строк: {total_rows}.",
            rows_affected=total_rows,
        )
    except _RollbackSignal:
        return ExecuteGrantsResult(
            status="rolled_back",
            message=f"TEST_MODE: SQL откачен (ROLLBACK). Было бы затронуто строк: {total_rows}.",
            rows_affected=0,
        )
    except Exception as e:
        logger.exception("execute_grants_sql failed: %s", e)
        return ExecuteGrantsResult(
            status="error",
            message=f"Ошибка при исполнении SQL: {e}",
            rows_affected=0,
        )


class _RollbackSignal(Exception):
    """Сигнал для отката в TEST_MODE (внутри asyncpg transaction)."""


def _split_statements(sql: str) -> list[str]:
    """
    Разделяет SQL-скрипт на отдельные стейтменты по ';' за пределами строковых литералов.
    """
    statements: list[str] = []
    current: list[str] = []
    in_string = False
    escape_next = False
    for char in sql:
        if escape_next:
            current.append(char)
            escape_next = False
            continue
        if char == "\\":
            current.append(char)
            escape_next = True
            continue
        if char == "'":
            in_string = not in_string
            current.append(char)
            continue
        if char == ";" and not in_string:
            stmt = "".join(current).strip()
            if stmt and not stmt.startswith("--"):
                statements.append(stmt)
            current = []
            continue
        current.append(char)
    remainder = "".join(current).strip()
    if remainder and not remainder.startswith("--"):
        statements.append(remainder)
    return statements
