"""
User creation pipeline: prepare → confirm → execute.

Prepare: check_email_exists, resolve module/group IDs, build sql_queries (clone or create).
Confirm: rebuild final sql_queries from user-edited data.
Execute: run sql_queries in one transaction (like execute_grants_sql); optionally return employee_id.
"""

from __future__ import annotations

import json
import logging
import re

from app.api.schemas import (
    ConfirmUserRequestBody,
    ConfirmUserResult,
    ExecuteUserResult,
    GrantForReview,
    GroupAssignment,
    GroupForReview,
    ModuleForReview,
    PrepareUserRequestBody,
    SourceEmployeeForReview,
    UserPreparationResult,
)
from app.core.config import settings
from app.core.database import get_pool
from app.core.exceptions import DatabaseError
from app.services.db_service import (
    check_email_exists,
    get_all_cities,
    get_all_modules,
    get_all_positions,
    get_city_name,
    get_companies,
    get_default_office_id_for_company,
    get_employee_display_info,
    get_employee_id_by_email,
    get_position_info,
    search_grant,
    search_group,
    search_users_by_fios,
)
from app.services.llm import create_chat_completion

logger = logging.getLogger(__name__)

# LLM: извлечь из промпта структуру для создания пользователя
_USER_CREATION_EXTRACT_SYSTEM = """Ты извлекаешь из сообщения пользователя данные для создания учётной записи.

Варианты запроса:
1) Клонирование: «создай такого же пользователя как X», «права как у ivanov@mail.ru», «клонируй пользователя 123» — нужен from_employee_id (число), from_employee_email (email шаблона) или from_employee_fio (ФИО для поиска). Плюс обязательно: email, fio, phone для нового пользователя.
2) Создание с нуля: ФИО, email, телефон, компания (название или id), модуль/модули (название или код), группа/группы (название), опционально город, должность.

Верни ТОЛЬКО валидный JSON без markdown и комментариев. Ключи:
- mode: "clone" | "create"
- email: string (обязательно)
- fio: string (обязательно)
- phone: string (обязательно)
- from_employee_id: number | null (для clone, если пользователь указал ID)
- from_employee_email: string | null (для clone, если указал email шаблонного пользователя — приоритет над ФИО)
- from_employee_fio: string | null (для clone, если указал ФИО — поиск по ФИО)
- company_id: number | null (для create)
- company_name: string | null (для create, если указано название компании)
- module_names: string[] (названия или коды модулей, например ["Офис", "CRM"])
- group_names: string[] (названия групп, например ["Менеджер"])
- group_company_id: number | null (company_id для всех групп, если одна компания)
- grant_names: string[] (названия точечных прав/грантов, например ["Материалы"], "права Материалы" → ["Материалы"])
- city_id: number | null (если пользователь указал ID города)
- city_name: string | null (если указал город по названию: "Астана", "Алматы")
- position_id: number | null (если пользователь указал ID должности)
- position_name: string | null (если указал должность по названию: "Бухгалтер", "Сотрудник Smart Remont")

Если что-то не указано — null или пустой массив. Телефон и email нормализуй: убрать лишние пробелы, email в нижний регистр."""


def _escape_sql(s: str) -> str:
    """Escape single quotes for SQL string literal."""
    return (s or "").replace("'", "''")


def _split_statements(sql: str) -> list[str]:
    """
    Split SQL script into statements by ';' outside string literals.
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


class _RollbackSignal(Exception):
    """Signal for rollback in TEST_MODE."""


# ── Prepare ─────────────────────────────────────────────────────────────────


async def _resolve_modules_for_review(module_ids: list[int]) -> list[ModuleForReview]:
    """Fetch module names for review list."""
    if not module_ids:
        return []
    all_mods = await get_all_modules()
    by_id = {m.module_id: m for m in all_mods}
    return [
        ModuleForReview(
            module_id=mid,
            module_name=by_id[mid].module_name if mid in by_id else f"Module {mid}",
            module_code=by_id[mid].module_code if mid in by_id else None,
        )
        for mid in module_ids
    ]


async def _resolve_groups_for_review(
    groups: list[GroupAssignment],
) -> list[GroupForReview]:
    """Fetch group names for review list."""
    if not groups:
        return []
    pool = get_pool()
    result: list[GroupForReview] = []
    async with pool.acquire() as conn:
        for g in groups:
            name = await conn.fetchval(
                "SELECT group_name FROM admin.group_tab WHERE group_id = $1",
                g.group_id,
            )
            result.append(
                GroupForReview(
                    group_id=g.group_id,
                    group_name=name or f"Group {g.group_id}",
                    company_id=g.company_id,
                )
            )
    return result


async def _resolve_grants_for_review(
    grant_ids: list[int], company_id: int = 1
) -> list[GrantForReview]:
    """Fetch grant names for review list."""
    if not grant_ids:
        return []
    pool = get_pool()
    result: list[GrantForReview] = []
    async with pool.acquire() as conn:
        for gid in grant_ids:
            row = await conn.fetchrow(
                """
                SELECT g.grant_id, g.grant_name
                FROM admin.grant_tab g
                WHERE g.grant_id = $1
                """,
                gid,
            )
            result.append(
                GrantForReview(
                    grant_id=gid,
                    grant_name=row["grant_name"] if row else f"Grant {gid}",
                    company_id=company_id,
                )
            )
    return result


def _build_clone_sql(from_employee_id: int, email: str, fio: str, phone: str) -> str:
    """Build single SQL call for admin.employee_clone."""
    e = _escape_sql(email.strip().lower())
    f = _escape_sql(fio.strip())
    p = _escape_sql(phone.strip())
    return (
        f"SELECT admin.employee_clone("
        f"from_employee_id_ := {from_employee_id}, "
        f"email_ := '{e}', "
        f"fio_ := '{f}', "
        f"phone_ := '{p}')"
    )


def _build_clone_plus_extras_sql(
    from_employee_id: int,
    email: str,
    fio: str,
    phone: str,
    module_ids: list[int],
    groups: list[GroupAssignment],
    grant_ids: list[int],
    company_id: int = 1,
) -> str:
    """
    Клонирование + дополнительная выдача модулей/групп/прав новому пользователю.
    Сначала employee_clone, затем по email находим new employee_id и делаем link/insert.
    """
    e = _escape_sql(email.strip().lower())
    statements: list[str] = [
        _build_clone_sql(from_employee_id, email, fio, phone),
        "CREATE TEMP TABLE IF NOT EXISTS _uc_new_employee (employee_id int)",
        "TRUNCATE _uc_new_employee",
        f"INSERT INTO _uc_new_employee SELECT employee_id FROM admin.employee_tab WHERE email = trim(lower('{e}')) LIMIT 1",
    ]
    for mid in module_ids:
        statements.append(
            f"SELECT admin.employee_module_link(module_id_ := {mid}, employee_id_ := (SELECT employee_id FROM _uc_new_employee))"
        )
    if groups:
        values = ", ".join(f"({g.group_id}, {g.company_id})" for g in groups)
        statements.append(
            f"INSERT INTO admin.employee_group_tab (employee_id, group_id, company_id, rowversion) "
            f"SELECT (SELECT employee_id FROM _uc_new_employee), v.gid, v.cid, now() FROM (VALUES {values}) AS v(gid, cid) "
            f"ON CONFLICT (employee_id, group_id, company_id) DO NOTHING"
        )
    if grant_ids:
        values = ", ".join(f"({gid}, {company_id})" for gid in grant_ids)
        statements.append(
            f"INSERT INTO admin.employee_grant_tab (employee_id, grant_id, company_id, rowversion) "
            f"SELECT (SELECT employee_id FROM _uc_new_employee), v.gid, v.cid, now() FROM (VALUES {values}) AS v(gid, cid) "
            f"ON CONFLICT (employee_id, grant_id, company_id) DO NOTHING"
        )
    return ";\n".join(statements)


def _build_create_sql(
    email: str,
    fio: str,
    phone: str,
    company_id: int,
    position_id: int,
    module_ids: list[int],
    groups: list[GroupAssignment],
    grant_ids: list[int],
    city_id: int | None,
    office_id: int | None,
    password_plain: str | None = None,
) -> str:
    """
    Build multi-statement SQL for create from scratch.
    password_plain: пароль для входа (телефон без первой цифры), считанный в Python — подставляется в md5(), чтобы совпадать с temporary_password.
    """
    company_id = company_id or 1
    position_id = position_id or 1
    e = _escape_sql(email.strip().lower())
    f = _escape_sql(fio.strip())
    p = _escape_sql(phone.strip())

    # Хэш пароля: если передан password_plain (вычислен в Python), используем его; иначе — выражение из телефона в SQL
    if password_plain is not None:
        pw_escaped = _escape_sql(password_plain)
        password_hash_sql = f"md5('{pw_escaped}' || 'dki#ds%$')"
    else:
        password_hash_sql = "md5(right((SELECT p FROM ph), length((SELECT p FROM ph)) - 1) || 'dki#ds%$')"

    statements: list[str] = [
        "CREATE TEMP TABLE IF NOT EXISTS _uc_new_employee (employee_id int)",
        "TRUNCATE _uc_new_employee",
    ]

    # INSERT employee and store new id in temp table (WITH with data-modifying CTE must be at top level)
    # selected_company_id required by check constraint employee_tab_selected_company_id_required
    # office_id required for офисных пользователей (is_smart=true)
    # employee_company_tab заполняется В ТОМ ЖЕ CTE, чтобы триггер видел компанию
    office_clause = f", office_id" if office_id is not None else ""
    office_value = f", {office_id}" if office_id is not None else ""
    insert_emp = f"""
    WITH ph AS (SELECT admin.get_phone_number('{p}') AS p),
         ins AS (
           INSERT INTO admin.employee_tab (
             email, fio, phone, password, is_active, company_id, selected_company_id, position_id{office_clause}, rowversion
           )
           SELECT
             '{e}',
             '{f}',
             (SELECT p FROM ph),
             {password_hash_sql},
             1,
             {company_id},
             {company_id},
             {position_id}{office_value},
             now()
           RETURNING employee_id
         ),
         company_link AS (
           INSERT INTO admin.employee_company_tab (employee_id, company_id, rowversion)
           SELECT employee_id, {company_id}, now() FROM ins
         )
    INSERT INTO _uc_new_employee
    SELECT employee_id FROM ins
    """
    statements.append(insert_emp.strip())

    for mid in module_ids:
        statements.append(
            f"SELECT admin.employee_module_link(module_id_ := {mid}, employee_id_ := (SELECT employee_id FROM _uc_new_employee))"
        )

    if groups:
        values = ", ".join(
            f"({g.group_id}, {g.company_id})" for g in groups
        )
        statements.append(
            f"INSERT INTO admin.employee_group_tab (employee_id, group_id, company_id, rowversion) "
            f"SELECT (SELECT employee_id FROM _uc_new_employee), v.gid, v.cid, now() FROM (VALUES {values}) AS v(gid, cid) "
            f"ON CONFLICT (employee_id, group_id, company_id) DO NOTHING"
        )

    if grant_ids:
        values = ", ".join(f"({gid}, {company_id})" for gid in grant_ids)
        statements.append(
            f"INSERT INTO admin.employee_grant_tab (employee_id, grant_id, company_id, rowversion) "
            f"SELECT (SELECT employee_id FROM _uc_new_employee), v.gid, v.cid, now() FROM (VALUES {values}) AS v(gid, cid) "
            f"ON CONFLICT (employee_id, grant_id, company_id) DO NOTHING"
        )

    if city_id is not None:
        statements.append(
            f"SELECT admin.employee_city_link(city_id_ := {city_id}, employee_id_ := (SELECT employee_id FROM _uc_new_employee))"
        )

    return ";\n".join(statements)


# ── Prepare from prompt (LLM → resolve IDs → prepare) ───────────────────────


async def _resolve_module_ids_by_names(module_names: list[str]) -> list[int]:
    """Резолв названий/кодов модулей в ID через get_all_modules."""
    if not module_names:
        return []
    all_mods = await get_all_modules()
    result: list[int] = []
    seen: set[int] = set()
    for name in module_names:
        name_clean = (name or "").strip().lower()
        if not name_clean:
            continue
        for m in all_mods:
            if m.module_id in seen:
                continue
            if (
                (m.module_name or "").strip().lower() == name_clean
                or (m.module_code or "").strip().lower() == name_clean
            ):
                result.append(m.module_id)
                seen.add(m.module_id)
                break
    return result


async def _resolve_group_ids_by_names(
    group_names: list[str],
    default_company_id: int = 1,
    module_id: int | None = None,
) -> list[GroupAssignment]:
    """Резолв названий групп в (group_id, company_id) через search_group. Если передан module_id — только группы этого модуля."""
    if not group_names:
        return []
    out: list[GroupAssignment] = []
    for name in group_names:
        name_clean = (name or "").strip()
        if not name_clean:
            continue
        found = await search_group(
            name_clean, employee_id=None, module_id=module_id
        )
        if found:
            out.append(
                GroupAssignment(
                    group_id=int(found[0]["group_id"]),
                    company_id=default_company_id,
                )
            )
    return out


async def _resolve_grant_ids_by_names(
    grant_names: list[str], module_id: int | None = None
) -> list[int]:
    """Резолв названий прав (грантов) в ID через search_grant. Если передан module_id — только гранты этого модуля."""
    if not grant_names:
        return []
    result: list[int] = []
    seen: set[int] = set()
    for name in grant_names:
        name_clean = (name or "").strip()
        if not name_clean:
            continue
        found = await search_grant(
            name_clean, employee_id=None, module_id=module_id
        )
        if found and found[0]["grant_id"] not in seen:
            result.append(int(found[0]["grant_id"]))
            seen.add(int(found[0]["grant_id"]))
    return result


async def _resolve_company_id_by_name(company_name: str | None) -> int | None:
    """Резолв названия компании в company_id через get_companies."""
    if not (company_name or "").strip():
        return None
    companies = await get_companies()
    name_clean = (company_name or "").strip().lower()
    for c in companies:
        cn = (c.get("company_name") or "").strip().lower()
        if cn == name_clean or name_clean in cn:
            return int(c.get("company_id", 0))
    return None


async def _resolve_position_id_by_name(position_name: str | None) -> int | None:
    """Резолв названия/кода должности в position_id через get_all_positions."""
    if not (position_name or "").strip():
        return None
    positions = await get_all_positions()
    name_clean = (position_name or "").strip().lower()
    for p in positions:
        pn = (p.position_name or "").strip().lower()
        pc = (p.position_code or "").strip().lower()
        if pn == name_clean or pc == name_clean or name_clean in pn:
            return p.position_id
    return None


async def _resolve_city_id_by_name(city_name: str | None) -> int | None:
    """Резолв названия города в city_id через get_all_cities."""
    if not (city_name or "").strip():
        return None
    cities = await get_all_cities()
    name_clean = (city_name or "").strip().lower()
    for c in cities:
        cn = (c.city_name or "").strip().lower()
        if cn == name_clean or name_clean in cn:
            return c.city_id
    return None


async def prepare_user_creation_from_prompt(prompt: str) -> UserPreparationResult:
    """
    Подготовка создания пользователя из текстового промпта.
    LLM извлекает сущности → резолвим имена в ID → вызываем prepare_user_creation.
    В ответ добавляем prepared_payload для селектов на фронте.
    """
    messages = [
        {"role": "system", "content": _USER_CREATION_EXTRACT_SYSTEM},
        {"role": "user", "content": (prompt or "").strip()},
    ]
    try:
        response = await create_chat_completion(
            messages,
            temperature=0.0,
            max_tokens=1024,
            tools=None,
        )
        content = (response.choices[0].message.content or "").strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```\s*$", "", content)
        data = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning("LLM user-creation extract JSON invalid: %s", e)
        raise DatabaseError("Не удалось разобрать запрос. Уточните: ФИО, email, телефон; компанию, модуль, группу — или «создай как у пользователя X».") from e
    except Exception as e:
        logger.exception("LLM user-creation extract failed: %s", e)
        raise DatabaseError(f"Ошибка при разборе запроса: {e}") from e

    mode = (data.get("mode") or "create").strip().lower()
    if mode not in ("clone", "create"):
        mode = "create"
    email = (data.get("email") or "").strip().lower()
    fio = (data.get("fio") or "").strip()
    phone = (data.get("phone") or "").strip()
    if not email or not fio or not phone:
        raise DatabaseError("В запросе должны быть указаны email, ФИО и телефон.")

    from_employee_id: int | None = None
    if mode == "clone":
        from_employee_id = data.get("from_employee_id")
        if from_employee_id is not None:
            from_employee_id = int(from_employee_id)
        if from_employee_id is None and data.get("from_employee_email"):
            from_employee_id = await get_employee_id_by_email(
                (data.get("from_employee_email") or "").strip()
            )
            if from_employee_id is None:
                raise DatabaseError(
                    "Не найден сотрудник для клонирования по email: "
                    + (data.get("from_employee_email") or "").strip()
                )
        if from_employee_id is None and data.get("from_employee_fio"):
            fios = [data.get("from_employee_fio").strip()]
            users = await search_users_by_fios(fios)
            if users:
                from_employee_id = users[0].employee_id
            else:
                raise DatabaseError(
                    "Не найден сотрудник для клонирования по ФИО: "
                    + (data.get("from_employee_fio") or "")
                )
        if mode == "clone" and from_employee_id is None:
            raise DatabaseError(
                "Для клонирования укажите ID, email или ФИО пользователя-шаблона."
            )

    company_id: int | None = data.get("company_id")
    if company_id is not None:
        company_id = int(company_id)
    if company_id is None and data.get("company_name"):
        company_id = await _resolve_company_id_by_name(data.get("company_name"))
    if company_id is None and mode == "create":
        company_id = 1

    module_ids: list[int] = []
    if data.get("module_names"):
        module_ids = await _resolve_module_ids_by_names(data.get("module_names") or [])
        if mode == "create" and not module_ids:
            raise DatabaseError("Не найдены модули по названиям: " + ", ".join(data.get("module_names") or []))

    # Группы и права — только из выбранного модуля (первый из запрошенных)
    primary_module_id: int | None = module_ids[0] if module_ids else None

    groups: list[GroupAssignment] = []
    if data.get("group_names"):
        default_cid = company_id if company_id is not None else 1
        group_company_id = data.get("group_company_id")
        if group_company_id is not None:
            default_cid = int(group_company_id)
        groups = await _resolve_group_ids_by_names(
            data.get("group_names") or [],
            default_company_id=default_cid,
            module_id=primary_module_id,
        )

    grant_ids: list[int] = []
    if data.get("grant_names"):
        grant_ids = await _resolve_grant_ids_by_names(
            data.get("grant_names") or [],
            module_id=primary_module_id,
        )

    city_id: int | None = data.get("city_id")
    if city_id is not None:
        city_id = int(city_id)
    if city_id is None and data.get("city_name"):
        city_id = await _resolve_city_id_by_name(data.get("city_name"))

    position_id: int | None = data.get("position_id")
    if position_id is not None:
        position_id = int(position_id)
    if position_id is None and data.get("position_name"):
        position_id = await _resolve_position_id_by_name(data.get("position_name"))

    body = PrepareUserRequestBody(
        mode=mode,
        email=email,
        fio=fio,
        phone=phone,
        from_employee_id=from_employee_id,
        company_id=company_id,
        module_ids=module_ids,
        groups=groups,
        grant_ids=grant_ids,
        city_id=city_id,
        position_id=position_id,
    )
    result = await prepare_user_creation(body)
    result.prepared_payload = body
    return result


async def prepare_user_creation(body: PrepareUserRequestBody) -> UserPreparationResult:
    """
    Step 1: Validate email, resolve IDs, build sql_queries for review.
    Does not execute anything.
    """
    mode = (body.mode or "").strip().lower()
    if mode not in ("clone", "create"):
        raise DatabaseError("mode должен быть clone или create")

    email = (body.email or "").strip().lower()
    fio = (body.fio or "").strip()
    phone = (body.phone or "").strip()
    if not email or not fio or not phone:
        raise DatabaseError("email, fio и phone обязательны")

    if await check_email_exists(email):
        raise DatabaseError("Пользователь с таким email уже существует")

    sql_queries: list[str] = []
    modules_for_review: list[ModuleForReview] = []
    groups_for_review: list[GroupForReview] = []
    grants_for_review: list[GrantForReview] = []
    visual_lines: list[str] = [f"Email: {email}, ФИО: {fio}, Телефон: {phone}"]

    source_employee_for_review: SourceEmployeeForReview | None = None
    if mode == "clone":
        if body.from_employee_id is None:
            raise DatabaseError("Для clone необходимо указать from_employee_id")
        clone_module_ids = list(body.module_ids) if body.module_ids else []
        clone_groups = list(body.groups) if body.groups else []
        clone_grant_ids = list(body.grant_ids) if body.grant_ids else []
        company_id_clone = body.company_id if body.company_id is not None else 1

        if clone_module_ids or clone_groups or clone_grant_ids:
            sql_queries.append(
                _build_clone_plus_extras_sql(
                    body.from_employee_id,
                    email,
                    fio,
                    phone,
                    module_ids=clone_module_ids,
                    groups=clone_groups,
                    grant_ids=clone_grant_ids,
                    company_id=company_id_clone,
                )
            )
            modules_for_review = await _resolve_modules_for_review(clone_module_ids)
            groups_for_review = await _resolve_groups_for_review(clone_groups)
            grants_for_review = await _resolve_grants_for_review(
                clone_grant_ids, company_id_clone
            )
        else:
            sql_queries.append(
                _build_clone_sql(body.from_employee_id, email, fio, phone)
            )

        donor = await get_employee_display_info(body.from_employee_id)
        if donor:
            source_employee_for_review = SourceEmployeeForReview(
                employee_id=int(donor["employee_id"]),
                fio=(donor.get("fio") or "").strip() or f"ID {body.from_employee_id}",
                email=(donor.get("email") or "").strip() or "",
            )
            visual_lines.append(
                f"Копируем права от: {source_employee_for_review.fio}"
                + (f" ({source_employee_for_review.email})" if source_employee_for_review.email else "")
                + f", ID {body.from_employee_id}",
            )
        else:
            visual_lines.append(f"Клонирование с сотрудника ID {body.from_employee_id}")

        if clone_module_ids or clone_groups or clone_grant_ids:
            if modules_for_review:
                visual_lines.append(f"Дополнительно модули: {[m.module_name for m in modules_for_review]}")
            if groups_for_review:
                visual_lines.append(f"Дополнительно группы: {[g.group_name for g in groups_for_review]}")
            if grants_for_review:
                visual_lines.append(f"Дополнительно права: {[g.grant_name for g in grants_for_review]}")

    else:
        # create
        company_id = body.company_id if body.company_id is not None else 1
        module_ids = list(body.module_ids) if body.module_ids else []
        groups = list(body.groups) if body.groups else []
        grant_ids = list(body.grant_ids) if body.grant_ids else []
        if not module_ids:
            raise DatabaseError("Для create необходимо указать хотя бы один module_id")
        modules_for_review = await _resolve_modules_for_review(module_ids)
        groups_for_review = await _resolve_groups_for_review(groups)
        grants_for_review = await _resolve_grants_for_review(grant_ids, company_id)
        position_id = body.position_id if body.position_id is not None else 2  # 2 = Сотрудник Smart Remont
        city_id_create = body.city_id if body.city_id is not None else 1  # 1 = Астана по умолчанию
        
        # Определяем office_id для офисных пользователей (is_smart=true)
        office_id: int | None = None
        pos_info = await get_position_info(position_id)
        is_smart_val = pos_info.get("is_smart") if pos_info else None
        # is_smart может быть строкой "True"/"False" или boolean
        is_smart = is_smart_val in (True, 1, "True", "true", "1") if is_smart_val is not None else False
        if is_smart:
            office_id = await get_default_office_id_for_company(company_id)
            if office_id:
                visual_lines.append(f"Office ID: {office_id} (автоматически определён для офисного пользователя)")

        position_name = (pos_info.get("position_name") or "").strip() if pos_info else ""
        visual_lines.append(f"Должность: {position_name or position_id} (ID {position_id})")
        city_name = await get_city_name(city_id_create)
        visual_lines.append(f"Город: {city_name or city_id_create} (ID {city_id_create})")
        
        password_plain = _temporary_password_from_phone(phone)
        one_sql = _build_create_sql(
            email=email,
            fio=fio,
            phone=phone,
            company_id=company_id,
            position_id=position_id,
            module_ids=module_ids,
            groups=groups,
            grant_ids=grant_ids,
            city_id=city_id_create,
            office_id=office_id,
            password_plain=password_plain,
        )
        sql_queries.append(one_sql)
        visual_lines.append(f"Модули: {[m.module_name for m in modules_for_review]}")
        visual_lines.append(f"Группы: {[g.group_name for g in groups_for_review]}")
        if grants_for_review:
            visual_lines.append(f"Права: {[g.grant_name for g in grants_for_review]}")

    visual_summary = "\n".join(visual_lines)
    return UserPreparationResult(
        sql_queries=sql_queries,
        visual_summary=visual_summary,
        modules_for_review=modules_for_review,
        groups_for_review=groups_for_review,
        grants_for_review=grants_for_review,
        source_employee_for_review=source_employee_for_review,
    )


# ── Confirm ──────────────────────────────────────────────────────────────────


async def confirm_user_creation(body: ConfirmUserRequestBody) -> ConfirmUserResult:
    """
    Step 2: Rebuild final sql_queries from user-confirmed data.
    Does not execute.
    """
    mode = (body.mode or "").strip().lower()
    if mode not in ("clone", "create"):
        raise DatabaseError("mode должен быть clone или create")

    email = (body.email or "").strip().lower()
    fio = (body.fio or "").strip()
    phone = (body.phone or "").strip()
    if not email or not fio or not phone:
        raise DatabaseError("email, fio и phone обязательны")

    sql_queries: list[str] = []
    visual_lines: list[str] = [f"Email: {email}, ФИО: {fio}, Телефон: {phone}"]

    if mode == "clone":
        if body.from_employee_id is None:
            raise DatabaseError("Для clone необходимо указать from_employee_id")
        clone_module_ids = list(body.module_ids) if body.module_ids else []
        clone_groups = list(body.groups) if body.groups else []
        clone_grant_ids = list(body.grant_ids) if body.grant_ids else []
        company_id_clone = body.company_id if body.company_id is not None else 1
        if clone_module_ids or clone_groups or clone_grant_ids:
            sql_queries.append(
                _build_clone_plus_extras_sql(
                    body.from_employee_id,
                    email,
                    fio,
                    phone,
                    module_ids=clone_module_ids,
                    groups=clone_groups,
                    grant_ids=clone_grant_ids,
                    company_id=company_id_clone,
                )
            )
            visual_lines.append(f"Клонирование с ID {body.from_employee_id} + доп. модули/группы/права")
        else:
            sql_queries.append(
                _build_clone_sql(body.from_employee_id, email, fio, phone)
            )
            visual_lines.append(f"Клонирование с сотрудника ID {body.from_employee_id}")
    else:
        company_id = body.company_id if body.company_id is not None else 1
        module_ids = list(body.module_ids) if body.module_ids else []
        groups = list(body.groups) if body.groups else []
        grant_ids = list(body.grant_ids) if body.grant_ids else []
        if not module_ids:
            raise DatabaseError("Для create необходимо указать хотя бы один module_id")
        position_id = body.position_id if body.position_id is not None else 2  # 2 = Сотрудник Smart Remont
        city_id_confirm = body.city_id if body.city_id is not None else 1  # 1 = Астана по умолчанию
        
        # Определяем office_id для офисных пользователей (is_smart=true)
        office_id: int | None = None
        pos_info = await get_position_info(position_id)
        is_smart_val = pos_info.get("is_smart") if pos_info else None
        is_smart = is_smart_val in (True, 1, "True", "true", "1") if is_smart_val is not None else False
        if is_smart:
            office_id = await get_default_office_id_for_company(company_id)
        
        password_plain = _temporary_password_from_phone(phone)
        one_sql = _build_create_sql(
            email=email,
            fio=fio,
            phone=phone,
            company_id=company_id,
            position_id=position_id,
            module_ids=module_ids,
            groups=groups,
            grant_ids=grant_ids,
            city_id=city_id_confirm,
            office_id=office_id,
            password_plain=password_plain,
        )
        sql_queries.append(one_sql)
        visual_lines.append(f"Company ID: {company_id}, Selected Company ID: {company_id}")
        if office_id:
            visual_lines.append(f"Office ID: {office_id}")
        visual_lines.append(f"Модули: {module_ids}, Группы: {len(groups)} шт., Права: {len(grant_ids)} шт.")

    return ConfirmUserResult(
        sql_queries=sql_queries,
        visual_summary="\n".join(visual_lines),
    )


# ── Execute ──────────────────────────────────────────────────────────────────


def _temporary_password_from_phone(phone: str | None) -> str | None:
    """
    Временный пароль для входа = номер телефона без первой цифры
    (как в admin.employee_clone и при создании с нуля).
    """
    if not phone or not phone.strip():
        return None
    digits = "".join(c for c in phone.strip() if c.isdigit())
    if len(digits) <= 1:
        return None
    password = digits[1:]
    logger.info(f"Generated temporary_password from phone '{phone}': digits='{digits}', password='{password}'")
    return password


async def execute_user_creation(
    sql_queries: list[str],
    email_for_lookup: str | None = None,
    phone: str | None = None,
    initiator_id: int | None = None,
) -> ExecuteUserResult:
    """
    Step 3: Execute sql_queries in one transaction.
    In TEST_MODE rolls back. Optionally returns employee_id by email and temporary_password by phone after success.
    Если передан initiator_id, устанавливает myapp.user_id для триггеров (проверка прав ADD_USER и др.).
    """
    pool = get_pool()
    total_rows = 0
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Устанавливаем session user для триггеров
                if initiator_id is not None:
                    await conn.execute(
                        "SELECT set_config('myapp.user_id', $1, false)",
                        str(initiator_id),
                    )
                for sql in sql_queries:
                    clean = sql.strip()
                    clean = re.sub(r"^\s*BEGIN\s*;\s*", "", clean, flags=re.IGNORECASE)
                    clean = re.sub(r"\s*COMMIT\s*;\s*$", "", clean, flags=re.IGNORECASE)
                    for statement in _split_statements(clean):
                        stmt = statement.strip()
                        if not stmt:
                            continue
                        result = await conn.execute(stmt)
                        if result:
                            parts = result.split()
                            if parts and parts[-1].isdigit():
                                total_rows += int(parts[-1])

                if settings.TEST_MODE:
                    raise _RollbackSignal()

        # After commit: optionally fetch employee_id by email; temporary_password from phone
        employee_id: int | None = None
        if email_for_lookup and not settings.TEST_MODE:
            async with pool.acquire() as conn:
                employee_id = await conn.fetchval(
                    "SELECT employee_id FROM admin.employee_tab WHERE email = trim(lower($1)) LIMIT 1",
                    email_for_lookup,
                )

        temporary_password = _temporary_password_from_phone(phone) if not settings.TEST_MODE else None

        return ExecuteUserResult(
            status="success",
            message=f"SQL выполнен. Затронуто строк: {total_rows}.",
            rows_affected=total_rows,
            employee_id=employee_id,
            temporary_password=temporary_password,
        )
    except _RollbackSignal:
        return ExecuteUserResult(
            status="rolled_back",
            message=f"TEST_MODE: SQL откачен (ROLLBACK). Было бы затронуто строк: {total_rows}.",
            rows_affected=0,
            employee_id=None,
            temporary_password=None,
        )
    except Exception as e:
        logger.exception("execute_user_creation failed: %s", e)
        return ExecuteUserResult(
            status="error",
            message=f"Ошибка при исполнении SQL: {e}",
            rows_affected=0,
            employee_id=None,
            temporary_password=None,
        )
