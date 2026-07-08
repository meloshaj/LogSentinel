"""LogSentinel core infrastructure — database engine, sessions, settings, and transactions."""

from .database import (
    AsyncSessionDep,
    check_pool_health,
    dispose_engine,
    get_async_session,
    get_engine,
    get_session_factory,
    init_engine,
)
from .orm import AnomalyEventRecord, Base, FeatureWindowRecord, LogRecord
from .settings import DatabaseSettings, get_database_settings
from .transaction import async_transactional, transactional

__all__: list[str] = [
    # Lifecycle
    "init_engine",
    "dispose_engine",
    "get_engine",
    "get_session_factory",
    "get_async_session",
    "AsyncSessionDep",
    "check_pool_health",
    # Settings
    "DatabaseSettings",
    "get_database_settings",
    # ORM
    "Base",
    "LogRecord",
    "FeatureWindowRecord",
    "AnomalyEventRecord",
    # Transactions
    "async_transactional",
    "transactional",
]
