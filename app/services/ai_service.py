from __future__ import annotations

import asyncio
import json
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings
from app.services import db_service
from app.services.data_store import ReferenceData


class AIServiceError(Exception):
    """Errors raised by AI service / LLM calls."""


client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


def _build_tools(reference_data: ReferenceData) -> list[dict[str, Any]]:
    """
    Define OpenAI tools (function calling) that map to our domain operations.

    Reference data is embedded in tool descriptions for better grounding.
    """
    # Use human-readable names from your exported JSON:
    # - admin_menu_tab:  menu_id, menu_name
    # - admin_group_tab: group_id, group_name
    # - admin_grant_tab: grant_id, grant_name
    menus_index = {m.get("menu_id"): m.get("menu_name") for m in reference_data.menus}
    groups_index = {g.get("group_id"): g.get("group_name") for g in reference_data.groups}
    grants_index = {g.get("grant_id"): g.get("grant_name") for g in reference_data.grants}

    return [
        {
            "type": "function",
            "function": {
                "name": "grant_group_access",
                "description": (
                    "Назначить сотруднику принадлежность к группе доступа. "
                    f"Доступные группы: {json.dumps(groups_index, ensure_ascii=False)[:2000]}"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "employee_id": {"type": "integer"},
                        "group_id": {"type": "integer"},
                    },
                    "required": ["employee_id", "group_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "add_menu_for_employee",
                "description": (
                    "Выдать сотруднику доступ к конкретному меню с определенным grant. "
                    f"Доступные меню: {json.dumps(menus_index, ensure_ascii=False)[:1500]}; "
                    f"доступные grants: {json.dumps(grants_index, ensure_ascii=False)[:1500]}"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "employee_id": {"type": "integer"},
                        "menu_id": {"type": "integer"},
                        "grant_id": {"type": "integer"},
                    },
                    "required": ["employee_id", "menu_id", "grant_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "link_module_for_employee",
                "description": "Назначить сотруднику доступ к модулю системы по module_id.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "employee_id": {"type": "integer"},
                        "module_id": {"type": "integer"},
                    },
                    "required": ["employee_id", "module_id"],
                },
            },
        },
    ]


async def process_user_request(
    *,
    user_id: int,
    prompt: str,
    reference_data: ReferenceData,
) -> dict[str, Any]:
    """
    High-level orchestrator.

    - Calls OpenAI with tools (function calling)
    - Executes selected tool(s) via db_service
    - Logs the interaction asynchronously
    """
    tools = _build_tools(reference_data)

    try:
        completion = await client.chat.completions.create(
            model="gpt-4.1-mini",
            temperature=0.0,
            max_tokens=512,
            tools=tools,
            tool_choice="auto",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты - AI-администратор прав доступа в системе Smart Remont. "
                        "Тебе дан user_id сотрудника (employee_id) и естественно-языковой запрос. "
                        "Твоя задача: определить, какие права нужно выдать, и вызвать подходящие инструменты. "
                        "Если нужно выдать несколько прав, можешь вызвать несколько инструментов подряд."
                    ),
                },
                {
                    "role": "user",
                    "content": f"user_id: {user_id}\nзапрос: {prompt}",
                },
            ],
        )
    except Exception as exc:  # noqa: BLE001
        raise AIServiceError(f"OpenAI API error: {exc}") from exc

    message = completion.choices[0].message

    tool_calls = message.tool_calls or []

    results: list[dict[str, Any]] = []

    async def _execute_tool_call(call: Any) -> dict[str, Any]:
        name = call.function.name
        # OpenAI передаёт аргументы как JSON-строку
        try:
            args = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        tool_result: dict[str, Any] = {"tool": name, "args": args, "status": "ok"}

        try:
            if name == "grant_group_access":
                await db_service.employee_group_link(
                    employee_id=int(args["employee_id"]),
                    group_id=int(args["group_id"]),
                )
            elif name == "add_menu_for_employee":
                await db_service.employee_menu_add(
                    employee_id=int(args["employee_id"]),
                    menu_id=int(args["menu_id"]),
                    grant_id=int(args["grant_id"]),
                )
            elif name == "link_module_for_employee":
                await db_service.employee_module_link(
                    employee_id=int(args["employee_id"]),
                    module_id=int(args["module_id"]),
                )
            else:
                tool_result["status"] = "ignored"
                tool_result["error"] = f"Unknown tool {name}"
        except Exception as exc:  # noqa: BLE001
            tool_result["status"] = "error"
            tool_result["error"] = str(exc)

        # Fire-and-forget async logging
        asyncio.create_task(
            db_service.log_ai_request(
                user_id=user_id,
                prompt=prompt,
                tool_name=name,
                tool_args=args,
                result_summary=tool_result.get("status"),
            )
        )

        return tool_result

    if tool_calls:
        results = await asyncio.gather(*[_execute_tool_call(tc) for tc in tool_calls])

    return {
        "id": completion.id,
        "tool_calls": results,
        "raw_ai_response": message.content,
    }

