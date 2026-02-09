"""
Pydantic request / response models for the API layer.
"""

from pydantic import BaseModel, Field


# ── Request ──────────────────────────────────────────────────────────────────


class ProcessRequestBody(BaseModel):
    user_id: int = Field(..., description="ID сотрудника в Smart Remont")
    prompt: str = Field(..., min_length=1, description="Естественно-языковой запрос о правах доступа")
    reason: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description="Причина запроса прав (минимум 10 символов). Объясните, зачем нужен доступ.",
    )


# ── Response ─────────────────────────────────────────────────────────────────


class ToolCallResult(BaseModel):
    tool: str = Field(..., description="Имя вызванного инструмента")
    args: dict = Field(default_factory=dict, description="Аргументы, переданные инструменту")
    status: str = Field(..., description="ok | error | ignored")
    error: str | None = Field(default=None, description="Сообщение об ошибке (если status != ok)")


class ProcessRequestResponse(BaseModel):
    id: str = Field(..., description="ID completion-запроса OpenAI")
    tool_calls: list[ToolCallResult] = Field(default_factory=list)
    explanation: str = Field(..., description="Человекочитаемое описание выполненных действий")
    ai_message: str | None = Field(default=None, description="Текстовый ответ модели (если есть)")
