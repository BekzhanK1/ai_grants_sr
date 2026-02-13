"""
API router — all HTTP endpoints live here.
"""

import logging

from app.api.dependencies import verify_api_key
from app.api.schemas import (
    AuditLogEntry,
    ConfirmGrantsRequestBody,
    ConfirmGrantsResult,
    ExecuteGrantsRequestBody,
    ExecuteGrantsResult,
    GrantsPreparationResult,
    ModuleDto,
    PrepareGrantsRequestBody,
    ProcessRequestBody,
    ProcessRequestResponse,
    ToolCallResult,
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
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.post("/process-request", response_model=ProcessRequestResponse)
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


@router.get("/logs", response_model=list[AuditLogEntry])
async def get_logs() -> list[AuditLogEntry]:
    """
    Возвращает последние 10 записей аудита AI.
    """
    rows = await get_recent_audit_logs(10)
    return [AuditLogEntry(**row) for row in rows]


@router.get("/modules", response_model=list[ModuleDto])
async def get_modules() -> list[ModuleDto]:
    """
    Возвращает все модули из admin.module_tab.
    """
    return await get_all_modules()


@router.post("/prepare-grants", response_model=GrantsPreparationResult)
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


@router.post("/confirm-grants", response_model=ConfirmGrantsResult)
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


@router.post("/execute-grants", response_model=ExecuteGrantsResult)
async def execute_grants(body: ExecuteGrantsRequestBody) -> ExecuteGrantsResult:
    """
    Шаг 3: Исполнение финального SQL.
    В TEST_MODE — откатывает транзакцию.
    """
    return await execute_grants_sql(sql_queries=body.sql_queries)
