## AI Admin Service (FastAPI + OpenAI)

Интеллектуальный backend для управления правами доступа в системе Smart Remont.
Сервис принимает естественно-языковой запрос и `user_id`, вызывает OpenAI с tools
и маппит результат на хранимые функции/процедуры в PostgreSQL (сейчас через заглушки).

### Стек

- **Backend**: `FastAPI`
- **DB**: PostgreSQL (через `asyncpg`, в коде обёртки под процедуры)
- **AI**: OpenAI Chat Completions + tools (`gpt-4.1-mini`)
- **Config**: `pydantic-settings`

### Структура проекта

- `app/main.py` — инициализация FastAPI, CORS, lifespan, health‑чек.
- `app/api/endpoints.py` — эндпоинт `POST /api/process-request`.
- `app/services/ai_service.py` — логика вызова OpenAI с tools и маппинг на DB‑слой.
- `app/services/db_service.py` — обёртки над хранимыми функциями (сейчас в режиме stub: печают вызовы вместо реальной БД).
- `app/services/data_store.py` — загрузка reference data (`admin_*_tab.json`) в память (singleton).
- `app/core/config.py` — конфигурация через `BaseSettings`.
- `json/` — reference data, экспортированные из базы (`admin_menu_tab.json`, `admin_group_tab.json`, `admin_grant_tab.json`).

### Установка

```bash
cd ai_grants_service
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Конфигурация

Создай `.env` по образцу `.env.example`:

```env
DATABASE_URL=postgresql://user:password@localhost:5432/smart_remont
OPENAI_API_KEY=sk-...
```

По умолчанию reference data читается из папки `json/` в корне проекта.

### Запуск

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

Проверка здоровья:

```bash
curl http://localhost:8000/health
```

### Основной эндпоинт

`POST /api/process-request`

Тело запроса:

```json
{
  "user_id": 123,
  "prompt": "Дай пользователю доступ к модулю отчётов и меню Финансы"
}
```

Пример ответа (упрощённо):

```json
{
  "id": "cmpl-...",
  "tool_calls": [
    {
      "tool": "grant_group_access",
      "args": { "employee_id": 123, "group_id": 1 },
      "status": "ok",
      "error": null
    }
  ],
  "explanation": "Назначены права с помощью инструментов: grant_group_access",
  "raw_ai_response": "..." 
}
```

В текущей версии DB‑вызовы работают через заглушки (`print`), поэтому сервис
подходит для демо и интеграции, не затрагивая живую базу. Для боевого режима
достаточно заменить stub‑реализацию в `db_service.py` на реальные вызовы БД.

