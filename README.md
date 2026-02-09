# AI Admin Service

Интеллектуальный backend-сервис для управления правами доступа в системе **Smart Remont**.  
Принимает естественно-языковой запрос + `user_id`, вызывает OpenAI (function calling) и маппит результат на хранимые процедуры PostgreSQL.

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
│   └── logging.py             # Настройка logging (file + console)
├── data/
│   ├── normalize.py           # Нормализация JSON-экспортов (BOM, типы)
│   └── reference.py           # ReferenceData — singleton, загрузка при старте
└── services/
    ├── ai_service.py          # Оркестратор: OpenAI → tool calls → db
    ├── db_service.py          # Стабы БД-операций (logging вместо SQL)
    ├── tool_definitions.py    # Описания tools для OpenAI
    └── tool_executor.py       # Диспатч tool-call → db_service
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
```

| Переменная | Обязательна | По умолчанию | Описание |
|---|---|---|---|
| `OPENAI_API_KEY` | да | — | Ключ OpenAI API |
| `OPENAI_MODEL` | нет | `gpt-4.1-mini` | Модель для chat completions |
| `DATA_DIR` | нет | `json` | Путь к папке с JSON-справочниками |

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

```json
{
  "user_id": 123,
  "prompt": "Добавь пользователя в группу Администраторы"
}
```

**Ответ:**

```json
{
  "id": "chatcmpl-...",
  "tool_calls": [
    {
      "tool": "grant_group_access",
      "args": { "employee_id": 123, "group_id": 1 },
      "status": "ok",
      "error": null
    }
  ],
  "explanation": "Назначены права: grant_group_access",
  "ai_message": null
}
```

## DB-слой

Сейчас все операции с базой — стабы, которые логируют вызовы через `logging.info`.  
Для перехода на боевой режим достаточно заменить тела функций в `db_service.py` на реальные asyncpg-вызовы хранимых процедур (SQL указан в docstrings каждой функции).
