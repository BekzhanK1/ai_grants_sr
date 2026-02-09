"""
Application entry point — FastAPI app factory with lifespan management.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import router as api_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.data.reference import load_reference_data


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup: configure logging + load reference data into app.state."""
    setup_logging()
    _app.state.reference_data = await load_reference_data(settings.DATA_DIR)
    yield


app = FastAPI(
    title="AI Admin Service",
    description="Intelligent access-rights management for Smart Remont",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")


@app.get("/health")
async def health_check() -> dict:
    return {"status": "ok"}
