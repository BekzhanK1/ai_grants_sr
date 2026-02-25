"""
Application settings loaded from environment / .env file.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenAI
    OPENAI_API_KEY: str = Field(..., description="API key for OpenAI")
    OPENAI_MODEL: str = Field(default="gpt-4.1-mini", description="Chat model to use")

    # PostgreSQL
    DATABASE_URL: str = Field(
        ...,
        description="PostgreSQL DSN, e.g. postgresql://user:pass@host:5432/dbname",
    )

    # TTL for reference data cache (seconds).  0 = never refresh automatically.
    REFERENCE_TTL: int = Field(
        default=300, description="Reference data cache TTL in seconds"
    )

    # Test Mode: if True, all write actions will be rolled back.
    TEST_MODE: bool = Field(
        default=False, description="Simulate actions without committing to DB"
    )

    # Admin approval mode for final SQL execution.
    # If True — API возвращает SQL со статусом pending_approval, без исполнения.
    # Если False — SQL выполняется сразу (при вызове execute_*).
    ADMIN_APPROVE: bool = Field(
        default=True,
        description=(
            "Require manual admin approval before executing final SQL. "
            "True → only generate SQL (status=pending_approval), "
            "False → execute SQL immediately."
        ),
    )

    DAILY_LIMIT_ON: bool = Field(
        default=True, description="Enable daily limit on requests"
    )

    API_KEY: str = Field(..., description="Secret API Key for authentication")

    # Telegram notifications for admin approval workflow
    TG_BOT_TOKEN: str | None = Field(
        default=None,
        description="Telegram Bot API token for sending notifications about AI requests",
    )
    TG_CHAT_ID: str | None = Field(
        default=None,
        description="Telegram chat ID (user or group) to receive AI request notifications",
    )


settings = Settings()
