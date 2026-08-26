"""Asynchronous database connection module for LogSentinel.

Uses SQLAlchemy 2.0 with the ``postgresql+asyncpg`` dialect for fully
non-blocking database access.  Connection pooling is tuned for a
microservices deployment behind an async FastAPI gateway.

**Lifecycle model** — The engine is no longer created at import time.
Call ``init_engine(settings)`` during application startup and
``dispose_engine()`` during shutdown to manage the pool cleanly.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .settings import DatabaseSettings

logger = logging.getLogger("logsentinel.database")

_CONNECTIVITY_MAX_RETRIES = 5
_CONNECTIVITY_BASE_DELAY = 1.0

# ---------------------------------------------------------------------------
# Module-level state — populated by init_engine(), cleared by dispose_engine()
# ---------------------------------------------------------------------------

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


# ---------------------------------------------------------------------------
# Lifecycle API
# ---------------------------------------------------------------------------


def init_engine(settings: DatabaseSettings) -> AsyncEngine:
    """Create the async engine and session factory from validated settings.

    Must be called exactly once during application startup (inside the
    FastAPI lifespan context manager).

    Returns the newly created engine for convenience.
    """
    global _engine, _session_factory

    if _engine is not None:
        logger.warning("init_engine() called while an engine already exists — disposing the old one first")
        # Can't await here so we just replace; the lifespan should call dispose first.

    _engine = create_async_engine(
        settings.url,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_pre_ping=True,
        pool_timeout=10.0,
        pool_recycle=settings.pool_recycle_seconds,
        echo=settings.echo_sql,
        connect_args={
            "timeout": 5.0,
            "command_timeout": 30.0,
        },
    )

    if settings.profiling_enabled:
        from .profiler import db_profiler
        db_profiler.attach_to_engine(_engine.sync_engine)

    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    logger.info(
        "Async engine created — pool_size=%d, max_overflow=%d, target=%s:%d/%s",
        settings.pool_size,
        settings.max_overflow,
        settings.host,
        settings.port,
        settings.db_name,
    )

    return _engine


async def verify_connectivity() -> None:
    """Probe the database with exponential backoff to confirm it accepts connections.

    Should be called once during application startup **after** ``init_engine()``.
    Tolerates container orchestration delays where TimescaleDB may not yet be
    ready despite the Docker healthcheck passing (race window).

    Raises ``RuntimeError`` after exhausting all retry attempts.
    """
    from sqlalchemy import text

    engine = get_engine()
    last_error: Exception | None = None

    for attempt in range(1, _CONNECTIVITY_MAX_RETRIES + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info(
                "Database connectivity verified on attempt %d/%d.",
                attempt,
                _CONNECTIVITY_MAX_RETRIES,
            )
            return
        except Exception as exc:
            last_error = exc
            delay = _CONNECTIVITY_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                "Database connectivity probe %d/%d failed (%s: %s). "
                "Retrying in %.1fs...",
                attempt,
                _CONNECTIVITY_MAX_RETRIES,
                type(exc).__name__,
                exc,
                delay,
            )
            await asyncio.sleep(delay)

    raise RuntimeError(
        f"FATAL: Could not connect to the database after "
        f"{_CONNECTIVITY_MAX_RETRIES} attempts. Last error: {last_error}"
    )


async def verify_schema_ready() -> None:
    """Verify the repository-owned Timescale schema before serving traffic.

    This is intentionally read-only.  Schema creation and ordered forward
    migrations are owned by ``scripts/database_lifecycle.py`` and must run as
    a bootstrap/migration-owner step before the restricted application user
    starts.  Failing closed here prevents a partially initialized database
    from being mistaken for a healthy application.
    """
    from sqlalchemy import text

    engine = get_engine()
    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        """
                        SELECT
                            EXISTS (
                                SELECT 1
                                FROM pg_tables
                                WHERE schemaname = 'public' AND tablename = 'logs'
                            ) AS logs_table,
                            EXISTS (
                                SELECT 1
                                FROM pg_tables
                                WHERE schemaname = 'public' AND tablename = 'schema_migrations'
                            ) AS migration_table,
                            EXISTS (
                                SELECT 1
                                FROM schema_migrations
                                WHERE version = '0000_canonical_init'
                            ) AS canonical_bootstrap,
                            EXISTS (
                                SELECT 1
                                FROM schema_migrations
                                WHERE version = '20260826_0001_multitenant_partitioning'
                            ) AS current_migration,
                            EXISTS (
                                SELECT 1
                                FROM pg_extension
                                WHERE extname = 'timescaledb'
                            ) AS timescale_extension,
                            EXISTS (
                                SELECT 1
                                FROM timescaledb_information.hypertables
                                WHERE hypertable_schema = 'public'
                                  AND hypertable_name = 'logs'
                            ) AS logs_hypertable
                        """
                    )
                )
            ).mappings().one()
    except Exception as exc:
        raise RuntimeError(
            "FATAL: canonical LogSentinel schema is not ready; run "
            "scripts/database_lifecycle.py --bootstrap/--apply with a schema "
            "owner before starting the application"
        ) from exc

    required_flags = (
        "logs_table",
        "migration_table",
        "canonical_bootstrap",
        "current_migration",
        "timescale_extension",
        "logs_hypertable",
    )
    missing = [flag for flag in required_flags if not bool(row[flag])]
    if missing:
        raise RuntimeError(
            "FATAL: canonical LogSentinel schema is incomplete; missing checks: "
            + ", ".join(missing)
            + ". Run scripts/database_lifecycle.py --apply."
        )


async def dispose_engine() -> None:
    """Dispose the async engine, draining all pooled connections.

    Must be called during application shutdown to release resources cleanly.
    After this call, ``get_engine()`` will raise until ``init_engine()`` is
    called again.
    """
    global _engine, _session_factory

    if _engine is None:
        logger.debug("dispose_engine() called but no engine exists — nothing to do")
        return

    await _engine.dispose()
    logger.info("Async engine disposed — all pooled connections drained")
    _engine = None
    _session_factory = None


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------


def get_engine() -> AsyncEngine:
    """Return the active async engine.

    Raises ``RuntimeError`` if ``init_engine()`` has not been called yet.
    """
    if _engine is None:
        raise RuntimeError(
            "Database engine is not initialized. "
            "Call init_engine(settings) during application startup."
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the active session factory.

    Raises ``RuntimeError`` if ``init_engine()`` has not been called yet.
    """
    if _session_factory is None:
        raise RuntimeError(
            "Session factory is not initialized. "
            "Call init_engine(settings) during application startup."
        )
    return _session_factory


# ---------------------------------------------------------------------------
# FastAPI Dependency — Async Session Generator
# ---------------------------------------------------------------------------


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an ``AsyncSession`` and guarantee cleanup.

    Intended for use as a FastAPI dependency via ``Depends(get_async_session)``.
    The session is closed in the ``finally`` block regardless of whether the
    request handler succeeds or raises.
    """
    factory = get_session_factory()
    session: AsyncSession = factory()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# Convenience type alias for route signatures
AsyncSessionDep = Annotated[AsyncSession, Depends(get_async_session)]


# ---------------------------------------------------------------------------
# Pool Health Introspection
# ---------------------------------------------------------------------------


def check_pool_health() -> dict[str, Any]:
    """Return a snapshot of the connection-pool status.

    Safe to call from diagnostics endpoints.  Returns a dict with pool
    statistics or an error indicator if the engine is not initialized.
    """
    if _engine is None:
        return {"initialized": False}

    pool = _engine.pool
    return {
        "initialized": True,
        "pool_size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
        "status": pool.status(),
    }
