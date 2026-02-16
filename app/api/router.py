"""
API router — all HTTP endpoints live here.
"""

import logging

from app.api.dependencies import verify_api_key
from app.api.schemas import (
    AuditLogEntry,
    ConfirmGrantsRequestBody,
    ConfirmGrantsResult,
    ConfirmUserRequestBody,
    ConfirmUserResult,
    ExecuteGrantsRequestBody,
    ExecuteGrantsResult,
    ExecuteUserRequestBody,
    ExecuteUserResult,
    GrantsPreparationResult,
    ModuleDto,
    PrepareFromPromptRequestBody,
    PrepareGrantsRequestBody,
    PrepareUserRequestBody,
    ProcessRequestBody,
    ProcessRequestResponse,
    ToolCallResult,
    UserPreparationResult,
)
from app.core.exceptions import AIServiceError, DatabaseError
from app.services.access_request import process_user_request
from app.services.db_service import get_recent_audit_logs
from app.services.grants_creation.service import (
    confirm_and_generate_sql,
    execute_grants_sql,
    get_all_modules,
    prepare_access_hierarchy,
)
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
    "/modules",
    response_model=list[ModuleDto],
    tags=["Reference"],
)
async def get_modules() -> list[ModuleDto]:
    """
    Возвращает все модули из admin.module_tab.
    """
    return await get_all_modules()


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
    """
    return await execute_grants_sql(sql_queries=body.sql_queries)


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
        )
    except DatabaseError as exc:
        logger.warning("user_creation execute failed: %s", exc)
        return JSONResponse(status_code=400, content={"detail": str(exc)})
