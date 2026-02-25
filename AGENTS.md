# AGENTS.md

## Cursor Cloud specific instructions

### Overview

This is **AI Admin Service** — a Python/FastAPI backend that processes natural-language access-rights requests via OpenAI function calling and executes the results against a PostgreSQL database. See `README.md` for full API docs and project structure.

### Required Secrets

| Secret | Purpose |
|--------|---------|
| `DATABASE_URL` | PostgreSQL DSN for the Smart Remont database |
| `OPENAI_API_KEY` | OpenAI API key (model: `gpt-4.1-mini`) |
| `API_KEY` | Secret key for `X-API-KEY` header auth |

### Running the application

The injected `DATABASE_URL` secret points to an external database that is **not reachable** from the Cloud Agent VM. To run locally, override it with a local PostgreSQL instance:

```bash
export DATABASE_URL="postgresql://<user>:<pass>@localhost:5432/smart_remont"
export TEST_MODE=True
export DAILY_LIMIT_ON=False
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8555 --reload
```

The local DB must have `admin` and `ai_admin` schemas with the required tables and stub PG functions.

### Local PostgreSQL setup

PostgreSQL 16 is installed. Start it with `sudo pg_ctlcluster 16 main start`, then create a database user and `smart_remont` database if they don't exist. Create the `admin` and `ai_admin` schemas with the required tables (see `app/services/db_service.py` and `app/data/reference.py` for the full list of expected tables and functions).

### Key gotchas

- **Env var precedence**: System env vars (injected secrets) override `.env` file values. You must `export DATABASE_URL=...` to use local PostgreSQL.
- **TEST_MODE**: When `True`, all DB write operations are rolled back. Always use `TEST_MODE=True` for development/testing.
- **Module-level side effects**: `app.services.db_service` prints TEST_MODE/DAILY_LIMIT status at import time, which is normal.
- **E2E tests** (`tests/scenario_suite.py`) require the running app on port 8555 **and** real employee data in the database. Tests 12 (validation), 13 (auth), and 14 (health) work with an empty/stub database.
- The test suite uses `requests` (not in `requirements.txt`); install it separately: `pip install requests`.

### Lint / Check commands

```bash
ruff check app/ tests/          # Linter (pre-existing warnings exist)
python3 -c "import app.main"    # Quick import check
```
