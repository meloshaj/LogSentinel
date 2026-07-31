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
        default="127.0.0.1",
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
        default=1800,
        ge=0,
        description="Seconds before a connection is recycled",
    )
    ssl_mode: str = Field(
        default="disable",
        description="PostgreSQL SSL mode (env: POSTGRES_SSL_MODE)",
    )
    echo_sql: bool = Field(
        default=False,
        description="Log all emitted SQL (env: SQL_ECHO)",
    )
    profiling_enabled: bool = Field(
        default=False,
        description="Enable database batch profiling (env: PROFILING_ENABLED)",
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
            f"@{self.host}:{self.port}/{self.db_name}?ssl={self.ssl_mode}"
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
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        db_name=os.getenv("POSTGRES_DB", "logsentinel_db"),
        pool_size=int(os.getenv("POOL_SIZE", "20")),
        max_overflow=int(os.getenv("POOL_MAX_OVERFLOW", "10")),
        pool_recycle_seconds=int(os.getenv("POOL_RECYCLE_SECONDS", "1800")),
        ssl_mode=os.getenv("POSTGRES_SSL_MODE", "disable"),
        echo_sql=os.getenv("SQL_ECHO", "false").lower() == "true",
        profiling_enabled=os.getenv("PROFILING_ENABLED", "false").lower() == "true",
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


class GraphScoringSettings(BaseModel):
    """Runtime controls for graph scoring integration."""

    enabled: bool = Field(
        default=True,
        description="Enable graph pathway scoring for anomaly alerts",
    )
    lookback_seconds: int = Field(
        default=180,
        gt=0,
        description="Seconds of recent anomaly/log evidence to inspect",
    )
    timeout_seconds: float = Field(
        default=2.0,
        gt=0.0,
        description="Maximum graph-analysis time per anomaly event",
    )
    max_anomaly_events: int = Field(
        default=500,
        gt=0,
        description="Maximum recent anomaly events to load per analysis",
    )
    max_log_records: int = Field(
        default=5000,
        gt=0,
        description="Maximum recent log rows to load for correlation evidence",
    )


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def get_graph_scoring_settings() -> GraphScoringSettings:
    """Construct graph-scoring runtime settings from environment variables."""
    return GraphScoringSettings(
        enabled=_parse_bool(os.getenv("GRAPH_SCORING_ENABLED"), default=True),
        lookback_seconds=int(os.getenv("GRAPH_SCORING_LOOKBACK_SECONDS", "180")),
        timeout_seconds=float(os.getenv("GRAPH_SCORING_TIMEOUT_SECONDS", "2.0")),
        max_anomaly_events=int(os.getenv("GRAPH_SCORING_MAX_ANOMALY_EVENTS", "500")),
        max_log_records=int(os.getenv("GRAPH_SCORING_MAX_LOG_RECORDS", "5000")),
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


class MicrosoftAuthSettings(BaseModel):
    """Typed configuration for Microsoft Entra ID authentication.

    When ``client_id`` is empty the Microsoft login endpoint is disabled
    safely and returns HTTP 503.  All other fields have secure defaults.

    Environment variables:
        AZURE_CLIENT_ID          — Application (client) ID from the Entra
                                   app registration.
        AZURE_TENANT_ID          — Tenant ID.  Use ``common`` for multi-tenant
                                   + personal accounts, ``organizations``
                                   for any Entra directory, or a specific
                                   GUID to restrict to a single tenant.
        AZURE_REQUIRED_SCOPE     — The delegated LogSentinel API scope that
                                   must appear in the ``scp`` claim of the
                                   incoming access token.
        AZURE_ALLOWED_TENANTS    — Optional comma-separated list of tenant
                                   GUIDs.  When non-empty only tokens
                                   issued by these tenants are accepted.
                                   Leave empty to accept any tenant
                                   matching the authority.
        AZURE_JWKS_TIMEOUT       — HTTP timeout (seconds) for fetching
                                   Microsoft OIDC metadata / JWKS keys.
        AZURE_JWKS_CACHE_TTL     — Seconds to cache the JWKS key set before
                                   re-fetching (allows key rotation).
    """

    client_id: str = Field(
        default="",
        description="Application (client) ID (env: AZURE_CLIENT_ID)",
    )
    tenant_id: str = Field(
        default="common",
        description="Tenant ID or 'common' / 'organizations' (env: AZURE_TENANT_ID)",
    )
    required_scope: str = Field(
        default="access_as_user",
        description="Required delegated API scope in the scp claim (env: AZURE_REQUIRED_SCOPE)",
    )
    allowed_tenants: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Optional allow-list of tenant GUIDs (env: AZURE_ALLOWED_TENANTS)",
    )
    jwks_timeout_seconds: float = Field(
        default=5.0,
        gt=0.0,
        description="HTTP timeout for OIDC / JWKS fetches (env: AZURE_JWKS_TIMEOUT)",
    )
    jwks_cache_ttl_seconds: int = Field(
        default=3600,
        gt=0,
        description="Seconds to cache the JWKS key set (env: AZURE_JWKS_CACHE_TTL)",
    )

    @property
    def enabled(self) -> bool:
        """Return whether Microsoft authentication is configured."""
        return bool(self.client_id)

    @property
    def authority(self) -> str:
        """Return the Microsoft identity v2.0 authority URL."""
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def openid_config_url(self) -> str:
        """Return the OIDC discovery endpoint."""
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0/.well-known/openid-configuration"

    @property
    def jwks_url(self) -> str:
        """Return the JWKS endpoint for the configured tenant."""
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"


def _split_csv(value: str | None) -> tuple[str, ...]:
    """Split a comma-separated env value into a tuple of trimmed non-empty strings."""
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def get_microsoft_auth_settings() -> MicrosoftAuthSettings:
    """Construct Microsoft authentication settings from the current environment."""
    return MicrosoftAuthSettings(
        client_id=os.getenv("AZURE_CLIENT_ID", ""),
        tenant_id=os.getenv("AZURE_TENANT_ID", "common"),
        required_scope=os.getenv("AZURE_REQUIRED_SCOPE", "access_as_user"),
        allowed_tenants=_split_csv(os.getenv("AZURE_ALLOWED_TENANTS")),
        jwks_timeout_seconds=float(os.getenv("AZURE_JWKS_TIMEOUT", "5.0")),
        jwks_cache_ttl_seconds=int(os.getenv("AZURE_JWKS_CACHE_TTL", "3600")),
    )
