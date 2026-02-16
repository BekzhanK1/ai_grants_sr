"""
Application entry point — FastAPI app factory with lifespan management.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.api.router import router as api_router
from app.core.database import close_pool, init_pool
from app.core.logging import setup_logging
from app.data.reference import load_reference_data
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup: logging → DB pool → reference data.  Shutdown: close pool."""
    setup_logging()
    await init_pool()
    await load_reference_data()  # initial fetch, cached with TTL
    yield
    await close_pool()


app = FastAPI(
    title="AI Admin Service",
    description="Intelligent access-rights management for Smart Remont",
    version="0.1.0",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "System", "description": "Health and availability"},
        {"name": "Reference", "description": "Справочники (модули и т.д.)"},
        {"name": "Access Request", "description": "Заявки на доступ (AI + tools)"},
        {
            "name": "Grants Creation",
            "description": "Подготовка и выдача прав по дереву",
        },
        {
            "name": "User Creation",
            "description": "Создание пользователя (clone или с нуля)",
        },
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")


@app.get("/health", tags=["System"])
async def health_check() -> dict:
    return {"status": "ok"}
