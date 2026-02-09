"""
Application settings loaded from environment / .env file.
"""

from pathlib import Path

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

    # Reference data directory (exported JSONs)
    DATA_DIR: Path = Field(default=Path("json"), description="Directory with JSON reference data")


settings = Settings()
