"""
API router — all HTTP endpoints live here.
"""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.dependencies import get_reference_data
from app.api.schemas import ProcessRequestBody, ProcessRequestResponse, ToolCallResult
from app.core.exceptions import AIServiceError, DatabaseError
from app.data.reference import ReferenceData
from app.services.ai_service import process_user_request

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/process-request", response_model=ProcessRequestResponse)
async def process_request(
    body: ProcessRequestBody,
    reference_data: ReferenceData = Depends(get_reference_data),
) -> ProcessRequestResponse | JSONResponse:
    """
    Принимает user_id + prompt, вызывает OpenAI с tools,
    исполняет выбранные инструменты и возвращает результат.
    """
    try:
        result = await process_user_request(
            user_id=body.user_id,
            prompt=body.prompt,
            reference_data=reference_data,
        )
    except (AIServiceError, DatabaseError) as exc:
        logger.exception("Domain error while processing request")
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # Build human-readable explanation
    ok_tools = [tc["tool"] for tc in result.get("tool_calls", []) if tc.get("status") == "ok"]
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
