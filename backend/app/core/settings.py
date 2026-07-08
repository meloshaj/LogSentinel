"""Centralized database configuration for LogSentinel.

All PostgreSQL-related settings are consolidated into a single validated
``DatabaseSettings`` model.  Environment variable names are kept identical
to the ones already used by ``docker-compose.yml`` and the previous
``database.py`` module so the migration is transparent.
"""

from __future__ import annotations

import os
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class DatabaseSettings(BaseModel):
    """Validated PostgreSQL connection and pool configuration.

    Every field can be overridden via its corresponding environment variable.
    The ``url`` property builds the full ``postgresql+asyncpg://`` DSN from
    the individual components.
    """

    user: str = Field(
        default="logsentinel",
        description="PostgreSQL user name (env: POSTGRES_USER)",
    )
    password: str = Field(
        default="logsentinel_secret",
        description="PostgreSQL password (env: POSTGRES_PASSWORD)",
    )
    host: str = Field(
        default="localhost",
        description="PostgreSQL hostname (env: POSTGRES_HOST)",
    )
    port: int = Field(
        default=5432,
        ge=1,
        le=65535,
        description="PostgreSQL port (env: POSTGRES_PORT)",
    )
    db_name: str = Field(
        default="logsentinel_db",
        description="PostgreSQL database name (env: POSTGRES_DB)",
    )

    # Connection-pool tuning
    pool_size: int = Field(
        default=20,
        ge=1,
        description="Number of persistent connections in the pool",
    )
    max_overflow: int = Field(
        default=10,
        ge=0,
        description="Max temporary connections above pool_size",
    )
    pool_recycle_seconds: int = Field(
        default=3600,
        ge=0,
        description="Seconds before a connection is recycled",
    )
    echo_sql: bool = Field(
        default=False,
        description="Log all emitted SQL (env: SQL_ECHO)",
    )

    # Optional full DSN override — takes precedence over individual fields
    database_url_override: Optional[str] = Field(
        default=None,
        description="Full DSN override (env: DATABASE_URL).  When set, "
        "individual host/port/user/password/db_name fields are ignored.",
    )

    @field_validator("port", mode="before")
    @classmethod
    def _coerce_port(cls, v: object) -> int:
        """Allow the port to arrive as a string from env-var sources."""
        if isinstance(v, str):
            return int(v)
        return int(v)  # type: ignore[arg-type]

    @property
    def url(self) -> str:
        """Build the async DSN string used by ``create_async_engine``."""
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql+asyncpg://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.db_name}"
        )


def get_database_settings() -> DatabaseSettings:
    """Construct ``DatabaseSettings`` from the current environment.

    This is the single place where ``os.getenv`` is called for database
    configuration.  All other modules should receive a ``DatabaseSettings``
    instance instead of reading environment variables directly.
    """
    return DatabaseSettings(
        user=os.getenv("POSTGRES_USER", "logsentinel"),
        password=os.getenv("POSTGRES_PASSWORD", "logsentinel_secret"),
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        db_name=os.getenv("POSTGRES_DB", "logsentinel_db"),
        pool_size=int(os.getenv("POOL_SIZE", "20")),
        max_overflow=int(os.getenv("POOL_MAX_OVERFLOW", "10")),
        pool_recycle_seconds=int(os.getenv("POOL_RECYCLE_SECONDS", "3600")),
        echo_sql=os.getenv("SQL_ECHO", "false").lower() == "true",
        database_url_override=os.getenv("DATABASE_URL"),
    )
