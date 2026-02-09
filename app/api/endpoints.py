from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.ai_service import process_user_request, AIServiceError
from app.services.db_service import DatabaseError
from app.services.data_store import ReferenceData


router = APIRouter()


class ProcessRequestPayload(BaseModel):
    user_id: int = Field(..., description="ID of the employee in Smart Remont")
    prompt: str = Field(..., description="Natural language request about access rights")


class ToolCallResult(BaseModel):
    tool: str
    args: dict
    status: str
    error: str | None = None


class ProcessRequestResponse(BaseModel):
    id: str
    tool_calls: list[ToolCallResult]
    explanation: str
    raw_ai_response: list | dict | str | None = Field(
        None, description="Raw content returned by the model (for debugging/demo)"
    )


def get_reference_data(request: Request) -> ReferenceData:
    return request.app.state.reference_data


@router.post("/process-request", response_model=ProcessRequestResponse)
async def process_request(
    payload: ProcessRequestPayload,
    request: Request,
    reference_data: ReferenceData = Depends(get_reference_data),
) -> JSONResponse:
    """
    Entry point for AI-powered access rights modifications.

    - Takes user_id and free-form prompt
    - Delegates to AI service, which will:
        * Call Anthropic Claude with tools (function calling)
        * Decide which DB operations to execute
        * Log the interaction and tool usage
    """
    try:
        result = await process_user_request(
            user_id=payload.user_id,
            prompt=payload.prompt,
            reference_data=reference_data,
        )
        # Human-friendly explanation for the demo / API consumer
        ok_tools = [tc["tool"] for tc in result.get("tool_calls", []) if tc.get("status") == "ok"]
        if ok_tools:
            explanation = (
                "Назначены права с помощью инструментов: " + ", ".join(sorted(set(ok_tools)))
            )
        elif not result.get("tool_calls"):
            explanation = "Модель не вызвала ни одного инструмента. Права не изменены."
        else:
            explanation = "Во время применения инструментов произошли ошибки."

        return ProcessRequestResponse(
            id=str(result.get("id", "")),
            tool_calls=[ToolCallResult(**tc) for tc in result.get("tool_calls", [])],
            explanation=explanation,
            raw_ai_response=result.get("raw_ai_response"),
        )

    except (DatabaseError, AIServiceError) as exc:
        # Known domain-level errors
        return JSONResponse(
            status_code=400,
            content={"detail": str(exc)},
        )

