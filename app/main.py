from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.endpoints import router as api_router
from app.core.config import settings
from app.services.data_store import load_reference_data, ReferenceData
from app.services.logging_service import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    FastAPI lifespan context.

    - Load reference data from /json on startup (singleton in-memory cache)
    - Perform any global setup (logging, etc.)
    """
    setup_logging()
    # Load and attach reference data directly to app.state
    app.state.reference_data = await load_reference_data(settings.DATA_DIR)

    yield

    # Place for graceful shutdown logic if needed (closing pools, etc.)


app = FastAPI(
    title="AI Admin Service",
    description="Intelligent access rights management for Smart Remont",
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

