"""LogSentinel core infrastructure — database engine, sessions, settings, and transactions."""

from .database import (
    AsyncSessionDep,
    check_pool_health,
    dispose_engine,
    get_async_session,
    get_engine,
    get_session_factory,
    init_engine,
    verify_connectivity,
    verify_schema_ready,
)
from .settings import (
    DatabaseSettings,
    IngestionSecuritySettings,
    get_database_settings,
    get_ingestion_security_settings,
)
from .transaction import async_transactional, transactional

__all__: list[str] = [
    # Lifecycle
    "init_engine",
    "dispose_engine",
    "verify_connectivity",
    "verify_schema_ready",
    "get_engine",
    "get_session_factory",
    "get_async_session",
    "AsyncSessionDep",
    "check_pool_health",
    # Settings
    "DatabaseSettings",
    "IngestionSecuritySettings",
    "get_database_settings",
    "get_ingestion_security_settings",
    # ORM
    "Base",
    "LogRecord",
    "FeatureWindowRecord",
    "AnomalyEventRecord",
    # Transactions
    "async_transactional",
    "transactional",
]


def __getattr__(name: str):
    """Load ORM symbols only when requested.

    Database-only tooling such as the migration runner must be able to import
    settings without loading encrypted ORM types.  The ORM module still fails
    closed when it is explicitly imported without ``ENCRYPTION_KEY``.
    """
    if name in {"Base", "LogRecord", "FeatureWindowRecord", "AnomalyEventRecord"}:
        from . import orm

        return getattr(orm, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
