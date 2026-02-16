"""
Pydantic request / response models for the API layer.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# ── Request ──────────────────────────────────────────────────────────────────


class ProcessRequestBody(BaseModel):
    user_id: int = Field(..., description="ID сотрудника в Smart Remont")
    prompt: str = Field(
        ..., min_length=1, description="Естественно-языковой запрос о правах доступа"
    )
    reason: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description="Причина запроса прав (минимум 10 символов). Объясните, зачем нужен доступ.",
    )


# ── Response ─────────────────────────────────────────────────────────────────


class ToolCallResult(BaseModel):
    tool: str = Field(..., description="Имя вызванного инструмента")
    args: dict = Field(
        default_factory=dict, description="Аргументы, переданные инструменту"
    )
    status: str = Field(..., description="ok | error | ignored")
    error: str | None = Field(
        default=None, description="Сообщение об ошибке (если status != ok)"
    )
    entity_name: str | None = Field(
        default=None, description="Человекочитаемое имя объекта"
    )
    entity_id: int | None = Field(default=None, description="ID объекта")


class ProcessRequestResponse(BaseModel):
    id: str = Field(..., description="ID completion-запроса OpenAI")
    tool_calls: list[ToolCallResult] = Field(default_factory=list)
    explanation: str = Field(
        ..., description="Человекочитаемое описание выполненных действий"
    )
    ai_message: str | None = Field(
        default=None, description="Текстовый ответ модели (если есть)"
    )


class AuditLogEntry(BaseModel):
    log_id: int = Field(..., description="PK записи аудита")
    employee_id: int = Field(..., description="ID сотрудника (инициатор)")
    user_prompt: str = Field(..., description="Исходный запрос пользователя")
    business_reason: str = Field(..., description="Обоснование запроса")
    ai_decision: list[dict[str, Any]] = Field(
        default_factory=list, description="Список действий AI"
    )
    ai_message: str | None = Field(default=None, description="Текстовый ответ AI")
    execution_status: str = Field(..., description="success | error | blocked")
    created_at: datetime = Field(..., description="Время создания записи")


# ── Grant (admin.grant_tab) ───────────────────────────────────────────────────


class GrantDto(BaseModel):
    """Одна запись из admin.grant_tab (право доступа по модулю)."""

    grant_id: int = Field(..., description="Идентификатор доступа")
    grant_pid: int | None = Field(default=None, description="Идентификатор родителя")
    grant_name: str = Field(..., description="Наименование доступа")
    order_num: int | None = Field(default=None, description="Порядковый номер в списке")
    grant_code: str = Field(..., description="Код доступа")
    is_active: int = Field(
        ...,
        description="Признак активности. 0 — не активный, 1 — активный. По умолчанию 1",
    )
    rowversion: datetime = Field(..., description="Дата и время изменения")
    last_checked: datetime | None = Field(
        default=None,
        description="Время, когда последний раз запрашивалось право",
    )
    child_cnt: int = Field(..., description="Кол-во дочерних записей")
    module_id: int = Field(..., description="ID модуля")
    is_super_grant: bool | None = Field(default=None, description="Признак супер-права")
    user_right_id: int | None = Field(
        default=None, description="ID в справочнике прав пользователя"
    )
    script_info: str | None = Field(
        default=None, description="Описание/категория для скриптов"
    )
    is_can_be_parent: bool = Field(..., description="Может ли право быть родителем")

    model_config = {"from_attributes": True}


class ModuleDto(BaseModel):
    """Одна запись из admin.module_tab (модуль)."""

    module_id: int = Field(..., description="Идентификатор модуля")
    module_name: str = Field(..., description="Наименование модуля")
    module_code: str = Field(..., description="Код модуля")

    model_config = {"from_attributes": True}


class PositionDto(BaseModel):
    """Одна запись из admin.position_tab (должность)."""

    position_id: int = Field(..., description="Идентификатор должности")
    position_name: str = Field(..., description="Наименование должности")
    position_code: str | None = Field(default=None, description="Код должности")
    is_smart: bool | str | None = Field(
        default=None, description="Офисный пользователь"
    )
    is_active: bool | str | None = Field(default=None, description="Активна")
    module_id: int | None = Field(default=None, description="Модуль должности")

    model_config = {"from_attributes": True}


class CityDto(BaseModel):
    """Одна запись из admin.city_tab (город)."""

    city_id: int = Field(..., description="Идентификатор города")
    city_name: str = Field(..., description="Наименование города")

    model_config = {"from_attributes": True}


class EmployeeDto(BaseModel):
    """
    Сотрудник с контекстом (employee_tab + position_tab + module_tab).
    """

    employee_id: int = Field(..., description="Идентификатор сотрудника")
    fio: str = Field(..., description="ФИО сотрудника")
    position_name: str | None = Field(default=None, description="Должность")
    module_name: str | None = Field(default=None, description="Модуль")

    model_config = {"from_attributes": True}


class EmployeeSearchItem(BaseModel):
    """Сотрудник для автокомплита (поиск по ФИО, similarity)."""

    employee_id: int = Field(..., description="ID сотрудника")
    fio: str = Field(..., description="ФИО")
    email: str | None = Field(default=None, description="Email")

    model_config = {"from_attributes": True}


# ── Grants preparation (дерево прав + LLM → new_grants, sql_queries) ──────────


class PrepareGrantsRequestBody(BaseModel):
    """Тело запроса на подготовку иерархии прав по дереву."""

    module_id: int = Field(..., description="ID модуля")
    prompt_text: str = Field(
        ...,
        min_length=1,
        description="Дерево прав по отступам (каждая строка: grant_code grant_name); теги [NEW]/[EXISTING] необязательны — система определяет сама. Блок Сотрудники/Кому — ФИО",
    )


class NewGrantItem(BaseModel):
    """Новый грант для INSERT в admin.grant_tab (по дереву)."""

    grant_code: str = Field(..., description="Код доступа")
    grant_name: str = Field(..., description="Наименование доступа")
    grant_pid: int | None = Field(
        default=None, description="ID родителя (если родитель из БД)"
    )
    parent_grant_code: str | None = Field(
        default=None,
        description="Код родителя, если родитель новый (для подзапроса в SQL)",
    )
    order_num: int | None = Field(default=None, description="Порядковый номер")
    is_can_be_parent: bool = Field(default=True, description="Может ли быть родителем")


class UserWithCompany(BaseModel):
    """Сотрудник с company_id для выдачи прав."""

    employee_id: int = Field(..., description="ID сотрудника")
    company_id: int | None = Field(
        default=None, description="ID компании (get_company_id)"
    )
    fio: str = Field(..., description="ФИО")
    position_name: str | None = Field(default=None, description="Должность")
    module_name: str | None = Field(default=None, description="Модуль")


class GrantsPreparationResult(BaseModel):
    """
    Результат подготовки иерархии прав (Delta-only: только создаваемое/меняемое).
    SQL не выполняется — возвращается для ревью пользователю.
    """

    new_grants: list[NewGrantItem] = Field(
        default_factory=list,
        description="Только гранты, которых нет в БД (будут созданы)",
    )
    existing_grants_in_tree: list[GrantDto] = Field(
        default_factory=list,
        description="Гранты из БД, которые встретились в дереве запроса ([EXISTING])",
    )
    users: list[UserWithCompany] = Field(
        default_factory=list,
        description="Найденные по ФИО сотрудники с employee_id и company_id",
    )
    sql_queries: list[str] = Field(
        default_factory=list,
        description="Компактные SQL (предварительные, для ревью)",
    )
    visual_tree: str = Field(
        default="",
        description="Схема дерева: существующие узлы — текст, новые — с тегом [NEW]",
    )


# ── Шаг 2: Подтверждение (пользователь отредактировал → генерация финального SQL) ──


class ConfirmGrantsRequestBody(BaseModel):
    """
    Пользователь ревьюит результат prepare-grants, может удалить из new_grants / users,
    затем отправляет подтверждённые данные на финальную генерацию SQL.
    visual_tree и all_grant_codes пересчитываются на сервере.
    """

    module_id: int = Field(..., description="ID модуля")
    new_grants: list[NewGrantItem] = Field(
        ..., description="Подтверждённые новые гранты (пользователь мог удалить лишние)"
    )
    existing_grants_in_tree: list[GrantDto] = Field(
        default_factory=list,
        description="Существующие гранты из дерева (для справки в SQL)",
    )
    users: list[UserWithCompany] = Field(
        ..., description="Подтверждённые пользователи (пользователь мог удалить лишних)"
    )


class ConfirmGrantsResult(BaseModel):
    """Результат подтверждения: финальный SQL."""

    sql_queries: list[str] = Field(
        default_factory=list,
        description="Финальный SQL-скрипт для исполнения",
    )
    visual_tree: str = Field(default="", description="Визуальное дерево")


# ── Шаг 3: Исполнение SQL ─────────────────────────────────────────────────


class ExecuteGrantsRequestBody(BaseModel):
    """Тело запроса на исполнение SQL (финальный шаг)."""

    sql_queries: list[str] = Field(..., description="SQL-скрипты для выполнения")


class ExecuteGrantsResult(BaseModel):
    """Результат исполнения SQL."""

    status: str = Field(..., description="success | error | rolled_back")
    message: str = Field(..., description="Описание результата")
    rows_affected: int = Field(default=0, description="Кол-во затронутых строк")


# ── User creation (prepare → confirm → execute) ──────────────────────────────


class PrepareFromPromptRequestBody(BaseModel):
    """Запрос на подготовку создания пользователя из текстового промпта."""

    prompt: str = Field(
        ...,
        min_length=1,
        description="Текст: кого создать (ФИО, email, телефон), в какую компанию, модуль, группу; либо «создай как у пользователя X» (клонирование)",
    )


class GroupAssignment(BaseModel):
    """Группа с company_id для привязки к сотруднику."""

    group_id: int = Field(..., description="ID группы")
    company_id: int = Field(..., description="ID компании")


class PrepareUserRequestBody(BaseModel):
    """Тело запроса на подготовку создания пользователя (шаг 1)."""

    mode: str = Field(..., description="clone | create")
    email: str = Field(..., min_length=1, description="Email нового пользователя")
    fio: str = Field(..., min_length=1, description="ФИО")
    phone: str = Field(..., min_length=1, description="Телефон")
    # clone
    from_employee_id: int | None = Field(
        default=None, description="ID сотрудника-шаблона (для mode=clone)"
    )
    # create
    company_id: int | None = Field(default=None, description="ID компании (для create)")
    module_ids: list[int] = Field(
        default_factory=list, description="ID модулей (для create, минимум 1)"
    )
    groups: list[GroupAssignment] = Field(
        default_factory=list, description="Группы с company_id (для create)"
    )
    grant_ids: list[int] = Field(
        default_factory=list, description="ID прав/грантов (для create, точечные права)"
    )
    city_id: int | None = Field(default=None, description="ID города (для create)")
    position_id: int | None = Field(
        default=None, description="ID должности (для create)"
    )


class ModuleForReview(BaseModel):
    """Модуль для отображения в ревью."""

    module_id: int = Field(..., description="ID модуля")
    module_name: str = Field(..., description="Название модуля")
    module_code: str | None = Field(default=None, description="Код модуля")


class GroupForReview(BaseModel):
    """Группа для отображения в ревью."""

    group_id: int = Field(..., description="ID группы")
    group_name: str = Field(..., description="Название группы")
    company_id: int = Field(..., description="ID компании")


class GrantForReview(BaseModel):
    """Право (грант) для отображения в ревью."""

    grant_id: int = Field(..., description="ID права")
    grant_name: str = Field(..., description="Название права")
    company_id: int = Field(..., description="ID компании")


class SourceEmployeeForReview(BaseModel):
    """Пользователь-шаблон для превью при клонировании (от кого копируем)."""

    employee_id: int = Field(..., description="ID сотрудника-шаблона")
    fio: str = Field(..., description="ФИО")
    email: str = Field(..., description="Email")


class UserPreparationResult(BaseModel):
    """Результат prepare: sql_queries и списки для ревью."""

    sql_queries: list[str] = Field(
        default_factory=list, description="SQL для ревью (не выполняется)"
    )
    visual_summary: str = Field(default="", description="Текстовая сводка для UI")
    modules_for_review: list[ModuleForReview] = Field(
        default_factory=list,
        description="Модули для ревью (можно убрать перед confirm)",
    )
    groups_for_review: list[GroupForReview] = Field(
        default_factory=list,
        description="Группы для ревью (можно убрать перед confirm)",
    )
    grants_for_review: list[GrantForReview] = Field(
        default_factory=list,
        description="Права (гранты) для ревью (можно убрать перед confirm)",
    )
    prepared_payload: PrepareUserRequestBody | None = Field(
        default=None,
        description="Структурированные данные для селектов на фронте (при prepare-from-prompt)",
    )
    source_employee_for_review: SourceEmployeeForReview | None = Field(
        default=None,
        description="При mode=clone: от кого копируем (ФИО, email, ID) для превью и редактирования на фронте",
    )


class ConfirmUserRequestBody(BaseModel):
    """Подтверждённые пользователем данные (после правки списков)."""

    mode: str = Field(..., description="clone | create")
    email: str = Field(..., min_length=1)
    fio: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=1)
    from_employee_id: int | None = Field(default=None)
    company_id: int | None = Field(default=None)
    module_ids: list[int] = Field(default_factory=list)
    groups: list[GroupAssignment] = Field(default_factory=list)
    grant_ids: list[int] = Field(default_factory=list)
    city_id: int | None = Field(default=None)
    position_id: int | None = Field(default=None)


class ConfirmUserResult(BaseModel):
    """Результат confirm: финальные sql_queries."""

    sql_queries: list[str] = Field(
        default_factory=list, description="Финальный SQL для исполнения"
    )
    visual_summary: str = Field(default="", description="Визуальная сводка")


class ExecuteUserRequestBody(BaseModel):
    """Тело запроса на исполнение SQL (шаг 3)."""

    sql_queries: list[str] = Field(..., description="SQL-скрипты для выполнения")
    email: str | None = Field(
        default=None,
        description="Email созданного пользователя (для возврата employee_id в ответе)",
    )
    phone: str | None = Field(
        default=None,
        description="Телефон созданного пользователя (для возврата временного пароля в ответе)",
    )
    initiator_id: int = Field(
        ...,
        description="ID сотрудника (админа), от имени которого выполняется операция. Устанавливается в myapp.user_id для триггеров.",
    )


class ExecuteUserResult(BaseModel):
    """Результат исполнения SQL создания пользователя."""

    status: str = Field(..., description="success | error | rolled_back")
    message: str = Field(..., description="Описание результата")
    rows_affected: int = Field(default=0, description="Кол-во затронутых строк")
    employee_id: int | None = Field(
        default=None, description="ID созданного сотрудника (при успехе)"
    )
    temporary_password: str | None = Field(
        default=None,
        description="Временный пароль для входа (телефон без первой цифры). Показать админу для передачи сотруднику.",
    )
