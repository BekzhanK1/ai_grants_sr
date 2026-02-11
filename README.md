# AI Admin Service

Интеллектуальный backend-сервис для управления правами доступа в системе **Smart Remont**.  
Принимает естественно-языковой запрос, причину и `user_id` (из аутентификации), вызывает OpenAI (function calling) и маппит результат на хранимые процедуры PostgreSQL.

---

## Как правильно писать запросы

**Запрос (`prompt`):**
- Описывайте **задачу**: что нужно делать, а не только «дать доступ».
- Указывайте **действие**: «ставить прорабов и бригады», «подписывать спецификации», «проверять остатки на складе».
- Можно несколько пунктов в одном запросе. Точные названия меню/грантов знать не обязательно — ИИ ищет по смыслу.

**Причина (`reason`):**
- Обязательно, **не короче 10 символов** (иначе 422).
- Одной фразой: зачем нужен доступ (роль, проект, задача). Без «asdf» и «123» — ИИ откажет.

**Примеры:**

```
✓ Мне нужно работать с договорами и подписывать спецификации. Подключите журнал договоров и право подписи.
  reason: Работа с документами по проектам: договоры и спецификации.

✓ Нужны права, чтобы ставить прорабов и бригады в ремонты. Назначать людей на объекты.
  reason: Управление персоналом на объектах ремонта.

✓ Нужно проверять остатки на складе и менять склад в заявке.
  reason: Логистика и работа с заявками поставщиков.

✗ prompt: "Бригады"  →  лучше: "Нужны права, чтобы ставить прорабов и бригады в ремонты."
✗ reason: "Работа"   →  лучше не короче 10 символов: "Работа в отделе качества."
```

---

## Стек

| Слой | Технология |
|------|------------|
| Framework | FastAPI |
| AI | OpenAI Chat Completions + tools (`gpt-4.1-mini`) |
| DB (production) | PostgreSQL / asyncpg |
| Config | pydantic-settings |
| Validation | Pydantic v2 |

## Структура проекта

```
app/
├── main.py                    # Точка входа, lifespan, middleware
├── api/
│   ├── router.py              # POST /api/process-request
│   ├── schemas.py             # Pydantic request / response модели
│   └── dependencies.py        # FastAPI Depends (reference data)
├── core/
│   ├── config.py              # Settings из .env
│   ├── exceptions.py          # AIServiceError, DatabaseError
│   ├── logging.py             # Настройка logging (file + console)
│   └── safety.py              # Чёрные списки (admin group, menu IDs)
├── data/
│   ├── normalize.py           # Нормализация JSON-экспортов (BOM, типы)
│   └── reference.py           # ReferenceData — singleton, загрузка при старте
└── services/
    ├── ai_service.py          # Оркестратор: OpenAI → tool calls → db
    ├── db_service.py          # Стабы БД-операций (logging вместо SQL)
    ├── tool_definitions.py    # Описания tools для OpenAI
    └── tool_executor.py       # Диспатч tool-call → db_service + safety checks
json/                          # Reference data (admin_*_tab.json)
```

## Установка

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Конфигурация

Создай `.env` по образцу `.env.example`:

```env
OPENAI_API_KEY=sk-...
DATABASE_URL=postgresql://user:pass@host:5432/smart_remont
API_KEY=your-secret-api-key
```

| Переменная | Обязательна | По умолчанию | Описание |
|---|---|---|---|
| `OPENAI_API_KEY` | да | — | Ключ OpenAI API |
| `OPENAI_MODEL` | нет | `gpt-4.1-mini` | Модель для chat completions |
| `DATABASE_URL` | да | — | PostgreSQL DSN |
| `API_KEY` | да | — | Секретный ключ для заголовка `X-API-KEY` |
| `TEST_MODE` | нет | `False` | Режим тестирования (rollback изменений) |
| `DAILY_LIMIT_ON` | нет | `True` | Лимит запросов в день на сотрудника |
| `REFERENCE_TTL` | нет | `300` | Время жизни кэша справочников (секунды) |

## Запуск

```bash
uvicorn app.main:app --reload
```

Health-check:

```bash
curl http://localhost:8000/health
```

## API

### `POST /api/process-request`

**Тело запроса:**

| Поле | Тип | Обязательно | Описание |
|------|-----|-------------|----------|
| `user_id` | `int` | да | ID сотрудника (приходит из слоя аутентификации) |
| `prompt` | `string` | да | Естественно-языковой запрос о правах доступа |
| `reason` | `string` | да | Причина запроса (10–500 символов). Зачем нужен доступ |

**Пример запроса:**

```json
{
  "user_id": 42,
  "prompt": "Открой мне доступ к модулю CRM",
  "reason": "Перехожу в отдел продаж, нужен CRM для работы с клиентами"
}
```

**Пример ответа:**

```json
{
  "id": "chatcmpl-...",
  "tool_calls": [
    {
      "tool": "link_module",
      "args": { "employee_id": 42, "module_id": 5 },
      "status": "ok",
      "error": null
    }
  ],
  "explanation": "Назначены права: link_module",
  "ai_message": "Доступ к модулю CRM (module_id=5) открыт для сотрудника 42."
}
```

**Если причина неадекватная:**

```json
{
  "id": "chatcmpl-...",
  "tool_calls": [],
  "explanation": "Модель не вызвала ни одного инструмента. Права не изменены.",
  "ai_message": "Указанная причина не объясняет бизнес-необходимость. Пожалуйста, опишите, для какой задачи вам нужен этот доступ."
}
```

Рекомендации по полям `prompt` и `reason` — см. раздел [Как правильно писать запросы](#как-правильно-писать-запросы) выше.

**Авторизация:** заголовок `X-API-KEY: <API_KEY>` (из `.env`). В Swagger: **Authorize** → ввести ключ.

## Безопасность

Сервис реализует многоуровневую защиту:

1. **Валидация на уровне схемы** — `reason` обязателен, минимум 10 символов (Pydantic 422 при нарушении).
2. **AI-проверка причины** — модель оценивает адекватность `reason` и отказывает, если причина бессмысленная или не связана с запросом.
3. **Fetch-before-action** — перед любым назначением AI запрашивает текущие права (`get_user_current_permissions`), чтобы не сработал Toggle-переключатель (повторный вызов `employee_group_link` / `employee_module_link` **удаляет** право).
4. **Чёрные списки** (`core/safety.py`) — запрещены: группа «Администраторы» (`group_id=1`), меню администрирования (`menu_id=1, 2, 4, 10`). Блокировка на уровне кода, даже если LLM сгенерирует запрещённый вызов.
5. **Проверка должности** — в контекст ИИ передаётся реальная должность и модуль сотрудника из БД; при несоответствии (например, пользователь пишет «Я из СБ», а в системе должность «Прораб») ИИ отказывает в выдаче прав.
6. **Аудит** — все значимые действия (кроме read-only) сохраняются в таблицу `ai_admin.audit_logs_tab`.

## Доступные инструменты AI

| Инструмент | PG-функция / таблица | Тип | Описание |
|---|---|---|---|
| `get_user_current_permissions` | SELECT из 4 таблиц | fetch | Текущие права сотрудника |
| `get_menu_by_url` | `admin.get_menu_by_url(url_)` | fetch | Найти menu_id по URL |
| `assign_role` | `admin.employee_group_link` | action (toggle) | Назначить роль |
| `add_interface_button` | `admin.employee_menu__add` | action (insert) | Открыть кнопку меню |
| `link_module` | `admin.employee_module_link` | action (toggle) | Привязать модуль |
| `add_grant` | `INSERT INTO admin.employee_grant_tab` | action (insert) | Выдать точечный грант |

## DB-слой

Операции с PostgreSQL выполняются через asyncpg. Для **нечёткого поиска** по названиям групп, меню и грантов (например «менеджера call-центра» → «Менеджер call-centra») используется расширение **pg_trgm**. Рекомендуется включить его в схеме `admin`(Уже есть):

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

Если расширение не установлено, поиск автоматически откатывается на обычный `ILIKE`.

## Аудит и логирование

Все изменения прав доступа (вызовы `assign_role`, `link_module` и др.) фиксируются в таблице `ai_admin.audit_logs_tab`.

**Схема таблицы:**

| Поле | Тип | Описание |
|---|---|---|
| `log_id` | `serial` | PK |
| `employee_id` | `int` | Инициатор запроса (он же получатель прав) |
| `user_prompt` | `text` | Исходный запрос ("дай доступ к...") |
| `business_reason` | `text` | Обоснование ("нужно для...") |
| `ai_decision` | `jsonb` | Список действий (tool + args + status + error + sql) |
| `ai_message` | `text` | Пояснение от AI (финальный ответ пользователю) |
| `execution_status` | `varchar` | Статус: `success`, `error`, `blocked` |
| `created_at` | `timestamp` | Время создания записи (default now()) |

## Тесты

E2E-сценарии (запрос к поднятому сервису):

```bash
# В .env должен быть API_KEY
python3 tests/scenario_suite.py
```

Опционально: `API_URL`, `REQUEST_TIMEOUT` — через переменные окружения.
