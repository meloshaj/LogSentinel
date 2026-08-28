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
from .orm import AnomalyEventRecord, Base, FeatureWindowRecord, LogRecord
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
