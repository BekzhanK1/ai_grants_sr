"""
Access request service — заявки на доступ (grants, menu, module).

Отдельный от grants_creation: пользователь пишет запрос + причину,
ИИ ищет права (search_menu, search_group, search_grant), проверяет текущие права
и выдаёт нужные (assign_role, add_interface_button, link_module, add_grant).

Общее: вызов GPT через app.services.llm, БД через app.services.db_service.
"""

from app.services.access_request.service import (
    preview_user_request,
    preview_user_request_from_selection,
    process_user_request,
)

__all__ = [
    "preview_user_request",
    "preview_user_request_from_selection",
    "process_user_request",
]
