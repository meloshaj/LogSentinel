"""Asynchronous database connection module for LogSentinel.

Uses SQLAlchemy 2.0 with the ``postgresql+asyncpg`` dialect for fully
non-blocking database access.  Connection pooling is tuned for a
microservices deployment behind an async FastAPI gateway.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger("logsentinel.database")

# ---------------------------------------------------------------------------
# Database URL construction from environment variables
# ---------------------------------------------------------------------------

_DB_USER: str = os.getenv("POSTGRES_USER", "logsentinel")
_DB_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "logsentinel_secret")
_DB_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
_DB_PORT: str = os.getenv("POSTGRES_PORT", "5432")
_DB_NAME: str = os.getenv("POSTGRES_DB", "logsentinel_db")

DATABASE_URL: str = (
    f"postgresql+asyncpg://{_DB_USER}:{_DB_PASSWORD}"
    f"@{_DB_HOST}:{_DB_PORT}/{_DB_NAME}"
)

# ---------------------------------------------------------------------------
# Async Engine
# ---------------------------------------------------------------------------

async_engine = create_async_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
)

logger.info(
    "Async engine created — pool_size=20, max_overflow=10, target=%s:%s/%s",
    _DB_HOST,
    _DB_PORT,
    _DB_NAME,
)

# ---------------------------------------------------------------------------
# Async Session Factory
# ---------------------------------------------------------------------------

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# ---------------------------------------------------------------------------
# FastAPI Dependency — Async Session Generator
# ---------------------------------------------------------------------------


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an ``AsyncSession`` and guarantee cleanup.

    Intended for use as a FastAPI dependency via ``Depends(get_async_session)``.
    The session is closed in the ``finally`` block regardless of whether the
    request handler succeeds or raises.
    """
    session: AsyncSession = AsyncSessionLocal()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# Convenience type alias for route signatures
AsyncSessionDep = Annotated[AsyncSession, Depends(get_async_session)]
