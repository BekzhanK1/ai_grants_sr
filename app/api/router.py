"""
API router — all HTTP endpoints live here.
"""

import logging
import json

from app.api.dependencies import verify_api_key
from app.api.schemas import (
    AccessRequestExecuteBody,
    AccessRequestExecuteResult,
    AccessRequestPrepareBody,
    AuditLogEntry,
    CityDto,
    ConfirmGrantsRequestBody,
    ConfirmGrantsResult,
    ConfirmUserRequestBody,
    ConfirmUserResult,
    EmployeeSearchItem,
    ExecuteGrantsRequestBody,
    ExecuteGrantsResult,
    ExecuteUserRequestBody,
    ExecuteUserResult,
    GrantsPreparationResult,
    ModuleDto,
    PositionDto,
    PrepareFromPromptRequestBody,
    PrepareGrantsRequestBody,
    PrepareUserRequestBody,
    ProcessRequestBody,
    ProcessRequestResponse,
    ToolCallResult,
    SqlApprovalRequestDto,
    SqlApprovalDecisionBody,
    UserPreparationResult,
)
from app.core.exceptions import AIServiceError, DatabaseError
from app.services.access_request import (
    preview_user_request,
    preview_user_request_from_selection,
    process_user_request,
)
from app.core.config import settings
from app.services.db_service import (
    create_sql_approval_request,
    get_all_cities,
    get_all_positions,
    get_employee_context,
    get_employee_current_menus_and_grants,
    get_employee_display_info,
    get_grants_by_module,
    get_menus_by_module,
    get_recent_audit_logs,
    list_sql_approval_requests,
    get_sql_approval_request,
    update_sql_approval_request_status,
)
from app.services.grants_creation.service import (
    confirm_and_generate_sql,
    execute_grants_sql,
    get_all_modules,
    prepare_access_hierarchy,
)
from app.services.telegram import send_telegram_message
from app.services.user_creation import (
    confirm_user_creation,
    execute_user_creation,
    prepare_user_creation,
    prepare_user_creation_from_prompt,
)
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.post(
    "/process-request",
    response_model=ProcessRequestResponse,
    tags=["Access Request"],
)
async def process_request(
    body: ProcessRequestBody,
) -> ProcessRequestResponse | JSONResponse:
    """
    Принимает user_id + prompt + reason, вызывает OpenAI с tools,
    исполняет выбранные инструменты и возвращает результат.
    """
    try:
        result = await process_user_request(
            user_id=body.user_id,
            prompt=body.prompt,
            reason=body.reason,
        )
    except (AIServiceError, DatabaseError) as exc:
        logger.exception("Domain error while processing request")
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # Build human-readable explanation
    ok_tools = [
        tc["tool"] for tc in result.get("tool_calls", []) if tc.get("status") == "ok"
    ]
    if ok_tools:
        explanation = "Назначены права: " + ", ".join(sorted(set(ok_tools)))
    elif not result.get("tool_calls"):
        explanation = "Модель не вызвала ни одного инструмента. Права не изменены."
    else:
        explanation = "Во время применения инструментов произошли ошибки."

    return ProcessRequestResponse(
        id=str(result.get("id", "")),
        tool_calls=[ToolCallResult(**tc) for tc in result.get("tool_calls", [])],
        explanation=explanation,
        ai_message=result.get("ai_message"),
    )


@router.get(
    "/access-request/menus",
    tags=["Access Request"],
)
async def access_request_menus(
    module_id: int,
) -> list[dict]:
    """Список меню модуля для ручного выбора (режим «по выбору»)."""
    return await get_menus_by_module(module_id)


@router.get(
    "/access-request/grants",
    tags=["Access Request"],
)
async def access_request_grants(
    module_id: int,
) -> list[dict]:
    """Список прав модуля для ручного выбора (режим «по выбору»)."""
    return await get_grants_by_module(module_id)


@router.get(
    "/access-request/current-permissions",
    tags=["Access Request"],
)
async def access_request_current_permissions(
    user_id: int,
) -> dict:
    """Текущие menu_ids и grant_ids сотрудника (employee + через группы), без дублей."""
    return await get_employee_current_menus_and_grants(user_id)


@router.post(
    "/access-request/preview",
    response_model=ProcessRequestResponse,
    tags=["Access Request"],
)
async def access_request_preview(
    body: AccessRequestPrepareBody,
) -> ProcessRequestResponse | JSONResponse:
    """
    Превью AI-заявки на доступ:
    - По запросу: prompt + reason → ИИ подбирает меню/права.
    - По выбору: module_id + menu_ids + grant_ids + reason → ИИ даёт вердикт.
    """
    use_selection = bool(body.menu_ids or body.grant_ids)
    if use_selection:
        if not body.module_id:
            return JSONResponse(
                status_code=400,
                content={"detail": "Для режима «по выбору» укажите module_id."},
            )
        try:
            result = await preview_user_request_from_selection(
                user_id=body.user_id,
                module_id=body.module_id,
                menu_ids=body.menu_ids or [],
                grant_ids=body.grant_ids or [],
                reason=body.reason,
            )
        except (AIServiceError, DatabaseError) as exc:
            logger.exception("Domain error while previewing access request (selection)")
            return JSONResponse(status_code=400, content={"detail": str(exc)})
    else:
        if not (body.prompt or "").strip():
            return JSONResponse(
                status_code=400,
                content={"detail": "Укажите запрос (prompt) или выберите меню/права."},
            )
        try:
            result = await preview_user_request(
                user_id=body.user_id,
                prompt=body.prompt or "",
                reason=body.reason,
                module_id=body.module_id,
            )
        except (AIServiceError, DatabaseError) as exc:
            logger.exception("Domain error while previewing access request")
            return JSONResponse(status_code=400, content={"detail": str(exc)})

    explanation = (
        "Предварительный план действий (SQL ещё не выполнен). "
        "Проверьте список действий перед исполнением."
    )

    return ProcessRequestResponse(
        id=str(result.get("id", "")),
        tool_calls=[ToolCallResult(**tc) for tc in result.get("tool_calls", [])],
        explanation=explanation,
        ai_message=result.get("ai_message"),
    )


@router.post(
    "/access-request/execute",
    response_model=AccessRequestExecuteResult,
    tags=["Access Request"],
)
async def access_request_execute(
    body: AccessRequestExecuteBody,
) -> AccessRequestExecuteResult | JSONResponse:
    """
    Исполнение заранее просмотренных действий (assign_role, add_grant и т.д.).
    При ADMIN_APPROVE=True действия не выполняются — заявка сохраняется в
    ai_admin.sql_approval_requests_tab и возвращается status=pending_approval.
    """
    if settings.ADMIN_APPROVE:
        try:
            actions_payload = [a.model_dump() for a in body.actions]
            request_id = await create_sql_approval_request(
                request_type="access_request",
                sql_queries=actions_payload,
                user_prompt=body.prompt,
                business_reason=body.reason,
                created_by=body.user_id,
            )
            # Уведомление в Telegram о новой заявке на доступ
            try:
                # Информация о сотруднике
                fio = None
                try:
                    emp = await get_employee_display_info(body.user_id)
                    if emp:
                        fio = emp.get("fio")
                except Exception:  # noqa: BLE001
                    fio = None

                # Краткий список меню/прав из payload
                menu_ids: set[int] = set()
                grant_ids: set[int] = set()
                for action in actions_payload:
                    if not isinstance(action, dict):
                        continue
                    tool = action.get("tool")
                    args = action.get("args") or {}
                    if tool == "add_interface_button" and "menu_id" in args:
                        try:
                            menu_ids.add(int(args["menu_id"]))
                        except (TypeError, ValueError):
                            continue
                    elif tool == "add_grant" and "grant_id" in args:
                        try:
                            grant_ids.add(int(args["grant_id"]))
                        except (TypeError, ValueError):
                            continue

                menu_part = ""
                grant_part = ""
                # Для лаконичности просто показываем ID; подробные названия уже есть в UI
                if menu_ids:
                    menu_part = "Меню ID: " + ", ".join(str(mid) for mid in sorted(menu_ids))
                if grant_ids:
                    grant_part = "Права ID: " + ", ".join(str(gid) for gid in sorted(grant_ids))

                text_lines = [
                    "🆕 <b>Новая AI-заявка на доступ</b>",
                    "",
                    f"ID заявки: <code>{request_id}</code>",
                    f"Тип: <code>access_request</code>",
                    f"Сотрудник: <b>{fio or 'неизвестно'}</b> (employee_id: <code>{body.user_id}</code>)",
                ]
                if menu_part:
                    text_lines.append("")
                    text_lines.append(menu_part)
                if grant_part:
                    text_lines.append("")
                    text_lines.append(grant_part)
                if body.prompt:
                    text_lines.append("")
                    text_lines.append(f"<b>Запрос:</b> {body.prompt}")
                if body.reason:
                    text_lines.append("")
                    text_lines.append(f"<b>Обоснование:</b> {body.reason}")
                await send_telegram_message("\n".join(text_lines))
            except Exception as notif_exc:  # noqa: BLE001
                logger.warning("Failed to send Telegram notification: %s", notif_exc)
        except Exception as e:
            logger.exception("create_sql_approval_request failed: %s", e)
            return JSONResponse(
                status_code=500,
                content={"detail": f"Не удалось сохранить заявку на аппрув: {e}"},
            )
        return AccessRequestExecuteResult(
            tool_calls=[],
            status="pending_approval",
            request_id=request_id,
            message="Ваш запрос был отправлен администратору.",
        )

    from app.services.access_request.service import execute_user_actions

    try:
        raw_results = await execute_user_actions(
            user_id=body.user_id,
            actions=[a.model_dump() for a in body.actions],
        )
    except (AIServiceError, DatabaseError) as exc:
        logger.exception("Domain error while executing access request actions")
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return AccessRequestExecuteResult(
        tool_calls=[ToolCallResult(**tc) for tc in raw_results],
    )


@router.get(
    "/logs",
    response_model=list[AuditLogEntry],
    tags=["Access Request"],
)
async def get_logs() -> list[AuditLogEntry]:
    """
    Возвращает последние 10 записей аудита AI.
    """
    rows = await get_recent_audit_logs(10)
    return [AuditLogEntry(**row) for row in rows]


@router.get(
    "/sql-approval-requests",
    response_model=list[SqlApprovalRequestDto],
    tags=["Admin"],
)
async def get_sql_approval_requests(
    request_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[SqlApprovalRequestDto]:
    """
    Список заявок из ai_admin.sql_approval_requests_tab (для админской страницы).
    Можно фильтровать по request_type и status.
    """
    rows = await list_sql_approval_requests(
        request_type=request_type,
        status=status,
        limit=limit,
    )
    return [SqlApprovalRequestDto(**row) for row in rows]


@router.post(
    "/sql-approval-requests/{request_id}/approve",
    tags=["Admin"],
)
async def approve_sql_approval_request(
    request_id: int,
    body: SqlApprovalDecisionBody,
) -> JSONResponse:
    """
    Одобрить заявку:
    - для access_request исполняет actions через execute_user_actions;
    - для остальных типов пока только меняет статус на approved.
    """
    req = await get_sql_approval_request(request_id)
    if not req:
        return JSONResponse(status_code=404, content={"detail": "Заявка не найдена"})
    if req["status"] not in {"pending", "approved"}:
        return JSONResponse(
            status_code=400,
            content={"detail": f"Заявка в статусе {req['status']}, менять нельзя"},
        )

    request_type = req.get("request_type")
    sql_payload = req.get("sql_queries") or []
    # sql_queries в БД хранится как jsonb; asyncpg может вернуть либо уже
    # распарсенный список, либо строку — в этом случае разбираем вручную.
    if isinstance(sql_payload, str):
        try:
            sql_payload = json.loads(sql_payload)
        except Exception:  # noqa: BLE001
            logger.warning(
                "approve_sql_approval_request: failed to json.loads sql_queries for request_id=%s",
                request_id,
            )
            sql_payload = []

    # По умолчанию просто помечаем заявку как approved
    new_status = "approved"

    # Для access_request дополнительно исполняем actions
    if request_type == "access_request":
        from app.services.access_request.service import execute_user_actions

        user_id = req.get("created_by")
        if not user_id:
            return JSONResponse(
                status_code=400,
                content={"detail": "У заявки не указан created_by"},
            )
        try:
            actions = sql_payload if isinstance(sql_payload, list) else []
            logger.info(
                "approve_sql_approval_request: executing %d action(s) for request_id=%s user_id=%s",
                len(actions),
                request_id,
                user_id,
            )
            await execute_user_actions(user_id=user_id, actions=actions)
            new_status = "executed"
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to execute access_request actions for request_id=%s", request_id)
            await update_sql_approval_request_status(
                request_id=request_id,
                status="failed",
                approved_by=body.admin_id,
                comment=body.comment or f"Ошибка исполнения: {exc}",
            )
            return JSONResponse(
                status_code=500,
                content={"detail": f"Не удалось исполнить действия: {exc}"},
            )

    await update_sql_approval_request_status(
        request_id=request_id,
        status=new_status,
        approved_by=body.admin_id,
        comment=body.comment,
    )
    return JSONResponse(status_code=200, content={"status": new_status})


@router.post(
    "/sql-approval-requests/{request_id}/reject",
    tags=["Admin"],
)
async def reject_sql_approval_request(
    request_id: int,
    body: SqlApprovalDecisionBody,
) -> JSONResponse:
    """
    Отклонить заявку без выполнения SQL.
    """
    req = await get_sql_approval_request(request_id)
    if not req:
        return JSONResponse(status_code=404, content={"detail": "Заявка не найдена"})
    if req["status"] not in {"pending", "approved"}:
        return JSONResponse(
            status_code=400,
            content={"detail": f"Заявка в статусе {req['status']}, менять нельзя"},
        )

    await update_sql_approval_request_status(
        request_id=request_id,
        status="rejected",
        approved_by=body.admin_id,
        comment=body.comment,
    )
    return JSONResponse(status_code=200, content={"status": "rejected"})


@router.get(
    "/modules",
    response_model=list[ModuleDto],
    tags=["Reference"],
)
async def get_modules(
    employee_id: int | None = None,
) -> list[ModuleDto]:
    """
    Возвращает модули из admin.module_tab.
    Если передан employee_id — только модули, доступные этому сотруднику
    (по admin.employee_module_tab). Используется для «AI заявки на доступ».
    Без employee_id — все модули (для создания грантов и т.д.).
    """
    return await get_all_modules(employee_id=employee_id)


@router.get(
    "/positions",
    response_model=list[PositionDto],
    tags=["Reference"],
)
async def get_positions() -> list[PositionDto]:
    """
    Возвращает активные должности из admin.position_tab (для выбора при создании пользователя).
    По умолчанию в user-creation используется position_id=2 (Сотрудник Smart Remont).
    """
    return await get_all_positions()


@router.get(
    "/cities",
    response_model=list[CityDto],
    tags=["Reference"],
)
async def get_cities() -> list[CityDto]:
    """
    Возвращает города из admin.city_tab (для привязки к пользователю).
    По умолчанию в user-creation используется city_id=1 (Астана).
    """
    return await get_all_cities()


@router.get(
    "/employees/search",
    response_model=list[EmployeeSearchItem],
    tags=["Reference"],
)
async def search_employees(
    q: str = "",
    limit: int = 10,
) -> list[EmployeeSearchItem]:
    """
    Поиск сотрудников по подстроке ФИО (pg_trgm similarity).
    Для автокомплита «Копируем права от»: топ до limit по similarity.
    """
    return await search_employees_for_autocomplete(query=q, limit=min(limit, 20))


@router.post(
    "/prepare-grants",
    response_model=GrantsPreparationResult,
    tags=["Grants Creation"],
)
async def prepare_grants(body: PrepareGrantsRequestBody) -> GrantsPreparationResult:
    """
    Шаг 1: Подготовка иерархии прав по дереву.
    Возвращает new_grants, existing_grants_in_tree, users, sql_queries, visual_tree.
    Пользователь ревьюит ответ, может удалить лишнее и отправить на /confirm-grants.
    """
    return await prepare_access_hierarchy(
        module_id=body.module_id,
        prompt_text=body.prompt_text,
    )


@router.post(
    "/confirm-grants",
    response_model=ConfirmGrantsResult,
    tags=["Grants Creation"],
)
async def confirm_grants(body: ConfirmGrantsRequestBody) -> ConfirmGrantsResult:
    """
    Шаг 2: Пользователь подтвердил данные (мог удалить new_grants / users).
    Генерирует финальный SQL через LLM. SQL не выполняется.
    """
    return await confirm_and_generate_sql(
        module_id=body.module_id,
        new_grants=body.new_grants,
        existing_grants_in_tree=body.existing_grants_in_tree,
        users=body.users,
    )


@router.post(
    "/execute-grants",
    response_model=ExecuteGrantsResult,
    tags=["Grants Creation"],
)
async def execute_grants(body: ExecuteGrantsRequestBody) -> ExecuteGrantsResult:
    """
    Шаг 3: Исполнение финального SQL.
    В TEST_MODE — откатывает транзакцию.
    При ADMIN_APPROVE заявка сохраняется в БД (user_prompt, business_reason) для аппрува админом.
    """
    return await execute_grants_sql(
        body.sql_queries,
        user_prompt=body.user_prompt,
        business_reason=body.business_reason,
        created_by=body.created_by,
    )


# ── User creation (prepare → confirm → execute) ─────────────────────────────


@router.post(
    "/user-creation/prepare-from-prompt",
    response_model=UserPreparationResult,
    tags=["User Creation"],
)
async def user_creation_prepare_from_prompt(
    body: PrepareFromPromptRequestBody,
) -> UserPreparationResult | JSONResponse:
    """
    Подготовка создания пользователя из текстового промпта.
    ИИ извлекает ФИО, email, телефон, компанию, модуль, группу (или «как у пользователя X»).
    Возвращает sql_queries и prepared_payload с ID для селектов на фронте (можно поправить и отправить в confirm).
    """
    try:
        return await prepare_user_creation_from_prompt(body.prompt)
    except DatabaseError as exc:
        logger.warning("user_creation prepare-from-prompt failed: %s", exc)
        return JSONResponse(status_code=400, content={"detail": str(exc)})


@router.post(
    "/user-creation/prepare",
    response_model=UserPreparationResult,
    tags=["User Creation"],
)
async def user_creation_prepare(
    body: PrepareUserRequestBody,
) -> UserPreparationResult | JSONResponse:
    """
    Шаг 1: Подготовка создания пользователя (clone или create).
    Проверка email, разрешение имён, генерация sql_queries для ревью.
    """
    try:
        return await prepare_user_creation(body)
    except DatabaseError as exc:
        logger.warning("user_creation prepare failed: %s", exc)
        return JSONResponse(status_code=400, content={"detail": str(exc)})


@router.post(
    "/user-creation/confirm",
    response_model=ConfirmUserResult,
    tags=["User Creation"],
)
async def user_creation_confirm(
    body: ConfirmUserRequestBody,
) -> ConfirmUserResult | JSONResponse:
    """
    Шаг 2: Подтверждённые данные (пользователь мог отредактировать списки).
    Возвращает финальные sql_queries.
    """
    try:
        return await confirm_user_creation(body)
    except DatabaseError as exc:
        logger.warning("user_creation confirm failed: %s", exc)
        return JSONResponse(status_code=400, content={"detail": str(exc)})


@router.post(
    "/user-creation/execute",
    response_model=ExecuteUserResult,
    tags=["User Creation"],
)
async def user_creation_execute(
    body: ExecuteUserRequestBody,
) -> ExecuteUserResult | JSONResponse:
    """
    Шаг 3: Исполнение SQL создания пользователя.
    В TEST_MODE — откатывает транзакцию.
    """
    try:
        return await execute_user_creation(
            sql_queries=body.sql_queries,
            email_for_lookup=body.email,
            phone=body.phone,
            initiator_id=body.initiator_id,
            user_prompt=body.user_prompt,
            business_reason=body.business_reason,
        )
    except DatabaseError as exc:
        logger.warning("user_creation execute failed: %s", exc)
        return JSONResponse(status_code=400, content={"detail": str(exc)})
