from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = Field(..., description="PostgreSQL DSN for asyncpg")
    OPENAI_API_KEY: str = Field(..., description="API key for OpenAI models")

    # By default, use the exported reference JSONs under /json
    DATA_DIR: Path = Field(default=Path("json"), description="Directory with JSON reference data")


settings = Settings()

