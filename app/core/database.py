"""
Async PostgreSQL connection pool (asyncpg).

Usage:
    from app.core.database import get_pool

    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT 1")
"""

from __future__ import annotations

import logging

import asyncpg

from app.core.config import settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def init_pool(*, min_size: int = 2, max_size: int = 10) -> asyncpg.Pool:
    """Create the global connection pool.  Called once at startup."""
    global _pool  # noqa: PLW0603
    if _pool is not None:
        return _pool

    logger.info("Creating asyncpg pool → %s", settings.DATABASE_URL.split("@")[-1])
    _pool = await asyncpg.create_pool(
        dsn=settings.DATABASE_URL,
        min_size=min_size,
        max_size=max_size,
    )
    logger.info("asyncpg pool ready  (min=%d, max=%d)", min_size, max_size)
    return _pool


async def close_pool() -> None:
    """Gracefully close the pool.  Called at shutdown."""
    global _pool  # noqa: PLW0603
    if _pool is not None:
        await _pool.close()
        logger.info("asyncpg pool closed")
        _pool = None


def get_pool() -> asyncpg.Pool:
    """Return the active pool.  Raises if not initialised."""
    if _pool is None:
        raise RuntimeError("Database pool is not initialised. Call init_pool() first.")
    return _pool
