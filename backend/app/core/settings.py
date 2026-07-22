"""Centralized configuration for LogSentinel.

PostgreSQL-related settings are consolidated into a validated
``DatabaseSettings`` model.  Ingestion security settings stay environment
backed and stateless for machine-to-machine log ingestion.
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


class Drain3PipelineSettings(BaseModel):
    """Validated batching and graceful-drain settings for the Drain3 pipeline."""

    batch_size: int = Field(
        default=500,
        gt=0,
        description="Parsed logs per persistence batch (env: DRAIN3_BATCH_SIZE)",
    )
    flush_interval_seconds: float = Field(
        default=5.0,
        gt=0,
        description=(
            "Seconds between periodic parsed-log flushes "
            "(env: DRAIN3_FLUSH_INTERVAL_SECONDS)"
        ),
    )
    queue_drain_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        description=(
            "Maximum graceful-shutdown queue drain wait "
            "(env: DRAIN3_QUEUE_DRAIN_TIMEOUT_SECONDS)"
        ),
    )


def get_drain3_pipeline_settings() -> Drain3PipelineSettings:
    """Construct Drain3 pipeline settings from the current environment."""
    return Drain3PipelineSettings(
        batch_size=int(os.getenv("DRAIN3_BATCH_SIZE", "500")),
        flush_interval_seconds=float(
            os.getenv("DRAIN3_FLUSH_INTERVAL_SECONDS", "5.0")
        ),
        queue_drain_timeout_seconds=float(
            os.getenv("DRAIN3_QUEUE_DRAIN_TIMEOUT_SECONDS", "30.0")
        ),
    )


class IngestionSecuritySettings(BaseModel):
    """Stateless API-key configuration for the ingestion endpoint."""

    api_keys: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Configured ingestion API keys from INGEST_API_KEY and INGEST_API_KEYS",
    )

    @property
    def configured(self) -> bool:
        """Return whether at least one ingestion API key is configured."""
        return bool(self.api_keys)


def _split_api_keys(value: str | None) -> list[str]:
    if not value:
        return []
    return [key.strip() for key in value.split(",") if key.strip()]


def get_ingestion_security_settings() -> IngestionSecuritySettings:
    """Construct ingestion security settings from the current environment."""
    keys: list[str] = []
    keys.extend(_split_api_keys(os.getenv("INGEST_API_KEY")))
    keys.extend(_split_api_keys(os.getenv("INGEST_API_KEYS")))

    return IngestionSecuritySettings(api_keys=tuple(dict.fromkeys(keys)))
