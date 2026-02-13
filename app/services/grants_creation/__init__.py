"""
Grants creation service — отдельный поток от process_user_request.

Принимает дерево прав (например M__Tier → M__TierBlock → M__TierBlockRead)
и список сотрудников; строит контекст из admin.grant_tab, отдаёт ИИ,
получает INSERT в grant_tab и employee_grant_tab.

Логика не смешивается с основным ai_service; вызов GPT — через app.services.llm.
"""

from app.services.grants_creation.service import process_grants_creation_request

__all__ = ["process_grants_creation_request"]
