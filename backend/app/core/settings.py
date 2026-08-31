"""Centralized configuration for LogSentinel.

PostgreSQL-related settings are consolidated into a validated
``DatabaseSettings`` model.  Ingestion security settings stay environment
backed and stateless for machine-to-machine log ingestion.
"""

from __future__ import annotations

import os
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.engine import make_url


class CoreSettings(BaseModel):
    """Core environment and domain configuration."""

    environment: str = Field(
        default="development", description="Deployment environment (env: ENVIRONMENT)"
    )
    domain_name: str | None = Field(
        default=None, description="Primary domain name (env: DOMAIN_NAME)"
    )
    frontend_url: tuple[str, ...] = Field(
        default_factory=tuple, description="Allowed frontend URLs (env: FRONTEND_URL)"
    )


def _split_csv(value: str | None) -> tuple[str, ...]:
    """Split a comma-separated env value into a tuple of trimmed non-empty strings."""
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def get_core_settings() -> CoreSettings:
    """Construct core settings from the current environment."""
    return CoreSettings(
        environment=os.getenv("ENVIRONMENT", "development"),
        domain_name=os.getenv("DOMAIN_NAME"),
        frontend_url=_split_csv(
            os.getenv("FRONTEND_URL", "http://localhost:5173,http://localhost:8080")
        ),
    )


class SMTPSettings(BaseModel):
    """SMTP configuration for transactional emails."""

    host: str = Field(
        default="localhost", description="SMTP server host (env: SMTP_HOST)"
    )
    port: int = Field(default=1025, description="SMTP server port (env: SMTP_PORT)")
    user: str = Field(default="", description="SMTP user (env: SMTP_USER)")
    password: str = Field(default="", description="SMTP password (env: SMTP_PASSWORD)")
    emails_from_email: str = Field(
        default="noreply@logsentinel.local",
        description="Sender email address (env: EMAILS_FROM_EMAIL)",
    )


def get_smtp_settings() -> SMTPSettings:
    return SMTPSettings(
        host=os.getenv("SMTP_HOST", "localhost"),
        port=int(os.getenv("SMTP_PORT", "1025")),
        user=os.getenv("SMTP_USER", ""),
        password=os.getenv("SMTP_PASSWORD", ""),
        emails_from_email=os.getenv("EMAILS_FROM_EMAIL", "noreply@logsentinel.local"),
    )


def _database_env(primary: str, *aliases: str, default: str) -> str:
    """Return the first configured database environment variable.

    ``POSTGRES_*`` is the canonical contract.  The ``DB_*`` aliases are kept
    only as a compatibility bridge for the standalone retraining worker and
    older local runbooks; all callers still receive one validated settings
    object.
    """
    for name in (primary, *aliases):
        value = os.getenv(name)
        if value is not None:
            return value
    return default


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
        default="",
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
    database_url_override: str | None = Field(
        default=None,
        description="Full DSN override (env: DATABASE_URL).  When set, "
        "individual host/port/user/password/db_name fields are ignored.",
    )

    @field_validator("port", mode="before")
    @classmethod
    def _coerce_port(cls, v: object) -> int:
        """Allow the port to arrive as a string from env-var sources."""
        if isinstance(v, int):
            return v
        return int(str(v))  # type: ignore[arg-type]

    @property
    def url(self) -> str:
        """Build the async DSN string used by ``create_async_engine``."""
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql+asyncpg://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.db_name}?ssl={self.ssl_mode}"
        )

    def asyncpg_connect_kwargs(self) -> dict[str, object]:
        """Return one consistent connection contract for direct asyncpg users.

        SQLAlchemy's asyncpg dialect accepts the ``postgresql+asyncpg`` URL,
        while ``asyncpg.connect`` requires a native ``postgresql``/``postgres``
        DSN or keyword arguments.  Normalising that boundary here prevents
        workers from silently diverging on host, database, or TLS settings.
        """
        common: dict[str, object] = {
            "timeout": 5.0,
            "command_timeout": 30.0,
        }

        if self.database_url_override:
            parsed = make_url(self.database_url_override)
            query = dict(parsed.query)
            ssl_query_value = query.pop("ssl", None)
            if ssl_query_value is not None and "sslmode" not in query:
                query["sslmode"] = ssl_query_value
            native_url = parsed.set(drivername="postgresql", query=query)
            return {
                "dsn": native_url.render_as_string(hide_password=False),
                **common,
            }

        return {
            "user": self.user,
            "password": self.password,
            "host": self.host,
            "port": self.port,
            "database": self.db_name,
            "ssl": False if self.ssl_mode == "disable" else self.ssl_mode,
            **common,
        }


def get_database_settings() -> DatabaseSettings:
    """Construct ``DatabaseSettings`` from the current environment.

    This is the single place where ``os.getenv`` is called for database
    configuration.  All other modules should receive a ``DatabaseSettings``
    instance instead of reading environment variables directly.
    """
    return DatabaseSettings(
        user=_database_env("POSTGRES_USER", "DB_USER", default="logsentinel"),
        password=_database_env("POSTGRES_PASSWORD", "DB_PASS", default=""),
        host=_database_env("POSTGRES_HOST", "DB_HOST", default="127.0.0.1"),
        port=int(_database_env("POSTGRES_PORT", "DB_PORT", default="5432")),
        db_name=_database_env("POSTGRES_DB", "DB_NAME", default="logsentinel_db"),
        pool_size=int(os.getenv("POSTGRES_POOL_SIZE", os.getenv("POOL_SIZE", "20"))),
        max_overflow=int(
            os.getenv("POSTGRES_MAX_OVERFLOW", os.getenv("POOL_MAX_OVERFLOW", "10"))
        ),
        pool_recycle_seconds=int(os.getenv("POOL_RECYCLE_SECONDS", "1800")),
        ssl_mode=_database_env("POSTGRES_SSL_MODE", "DB_SSL_MODE", default="disable"),
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
        flush_interval_seconds=float(os.getenv("DRAIN3_FLUSH_INTERVAL_SECONDS", "5.0")),
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

    api_keys: dict[str, str] = Field(
        default_factory=dict,
        description="Configured ingestion API keys mapping api_key -> tenant_id from INGEST_API_KEY and INGEST_API_KEYS",
    )

    @property
    def configured(self) -> bool:
        """Return whether at least one ingestion API key is configured."""
        return bool(self.api_keys)


def _split_api_keys(value: str | None) -> dict[str, str]:
    """Parse comma-separated API keys into a mapping of key -> tenant_id.

    Format: 'tenant_id:api_key' or just 'api_key' (defaults to tenant_id='default').
    """
    if not value:
        return {}

    result = {}
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            tenant_id, key = item.split(":", 1)
            result[key.strip()] = tenant_id.strip()
        else:
            result[item] = "default"
    return result


def get_ingestion_security_settings() -> IngestionSecuritySettings:
    """Construct ingestion security settings from the current environment."""
    keys: dict[str, str] = {}
    keys.update(_split_api_keys(os.getenv("INGEST_API_KEY")))
    keys.update(_split_api_keys(os.getenv("INGEST_API_KEYS")))

    return IngestionSecuritySettings(api_keys=keys)


class GitHubAuthSettings(BaseModel):
    """Configuration for GitHub OAuth."""

    client_id: str = Field(
        default="",
        description="GitHub OAuth Client ID (env: GITHUB_CLIENT_ID)",
    )
    client_secret: str = Field(
        default="",
        description="GitHub OAuth Client Secret (env: GITHUB_CLIENT_SECRET)",
    )
    callback_url: str = Field(
        default="",
        description="GitHub OAuth Callback URL (env: GITHUB_CALLBACK_URL)",
    )

    @property
    def enabled(self) -> bool:
        """Return whether GitHub authentication is configured."""
        return bool(self.client_id and self.client_secret and self.callback_url)


def get_github_auth_settings() -> GitHubAuthSettings:
    """Construct GitHub authentication settings from the current environment."""
    return GitHubAuthSettings(
        client_id=os.getenv("GITHUB_CLIENT_ID", ""),
        client_secret=os.getenv("GITHUB_CLIENT_SECRET", ""),
        callback_url=os.getenv("GITHUB_CALLBACK_URL", ""),
    )


class MicrosoftAuthSettings(BaseModel):
    """Typed configuration for Microsoft Entra ID authentication.

    When ``client_id`` or ``tenant_id`` is empty the Microsoft login endpoint
    is disabled safely and returns HTTP 503. Multitenant modes must be chosen
    explicitly; the application never defaults to ``common``.

    Environment variables:
        AZURE_CLIENT_ID          — Application (client) ID from the Entra
                                   app registration.
        AZURE_TENANT_ID          — Tenant ID.  Use ``common`` for multi-tenant
                                   + personal accounts, ``organizations``
                                   for any Entra directory, ``consumers``
                                   for personal Microsoft accounts only, or a
                                   specific GUID to restrict to one tenant.
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
        default="",
        description=(
            "Tenant GUID or 'common' / 'organizations' / 'consumers' "
            "(env: AZURE_TENANT_ID)"
        ),
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

    @field_validator("client_id")
    @classmethod
    def _normalize_client_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            return ""
        try:
            return str(UUID(normalized))
        except ValueError as exc:
            raise ValueError(
                "AZURE_CLIENT_ID must be an application client GUID"
            ) from exc

    @field_validator("required_scope")
    @classmethod
    def _validate_required_scope(cls, value: str) -> str:
        if value.strip() != "access_as_user":
            raise ValueError("AZURE_REQUIRED_SCOPE must be access_as_user")
        return "access_as_user"

    @field_validator("tenant_id")
    @classmethod
    def _validate_tenant_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            return ""
        if normalized in {"common", "organizations", "consumers"}:
            return normalized
        try:
            return str(UUID(normalized))
        except ValueError as exc:
            raise ValueError(
                "AZURE_TENANT_ID must be common, organizations, consumers, or a tenant GUID"
            ) from exc

    @field_validator("allowed_tenants")
    @classmethod
    def _validate_allowed_tenants(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            val_clean = value.strip().lower()
            if val_clean in {"common", "organizations", "consumers"}:
                normalized.append(val_clean)
                continue
            try:
                normalized.append(str(UUID(val_clean)))
            except ValueError as exc:
                raise ValueError(
                    "AZURE_ALLOWED_TENANTS entries must be tenant GUIDs or standard multi-tenant aliases"
                ) from exc
        return tuple(normalized)

    @property
    def enabled(self) -> bool:
        """Return whether Microsoft authentication is configured."""
        return bool(self.client_id and self.tenant_id)

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


def _split_csv_existing(value: str | None) -> tuple[str, ...]:
    """Split a comma-separated env value into a tuple of trimmed non-empty strings."""
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def get_microsoft_auth_settings() -> MicrosoftAuthSettings:
    """Construct Microsoft authentication settings from the current environment."""
    return MicrosoftAuthSettings(
        client_id=os.getenv("AZURE_CLIENT_ID", ""),
        tenant_id=os.getenv("AZURE_TENANT_ID", ""),
        required_scope=os.getenv("AZURE_REQUIRED_SCOPE", "access_as_user"),
        allowed_tenants=_split_csv_existing(os.getenv("AZURE_ALLOWED_TENANTS")),
        jwks_timeout_seconds=float(os.getenv("AZURE_JWKS_TIMEOUT", "5.0")),
        jwks_cache_ttl_seconds=int(os.getenv("AZURE_JWKS_CACHE_TTL", "3600")),
    )


class BenchmarkingSettings(BaseModel):
    """Configuration for benchmarking and stress-testing features."""

    enable_benchmarking_endpoints: bool = Field(
        default=False,
        description="Enable benchmarking and stress-testing API endpoints (env: ENABLE_BENCHMARKING_ENDPOINTS)",
    )


def get_benchmarking_settings() -> BenchmarkingSettings:
    """Construct Benchmarking settings from the current environment."""
    return BenchmarkingSettings(
        enable_benchmarking_endpoints=_parse_bool(
            os.getenv("ENABLE_BENCHMARKING_ENDPOINTS"), default=False
        ),
    )


class ArchiveSettings(BaseModel):
    """Configuration for S3 Hot/Cold Storage Archive."""

    s3_bucket_name: str = Field(
        default="logsentinel-archive",
        description="S3 bucket for cold storage (env: S3_BUCKET_NAME)",
    )
    s3_backup_bucket: str | None = Field(
        default=None,
        description="S3 backup bucket (env: S3_BACKUP_BUCKET)",
    )
    s3_endpoint_url: str | None = Field(
        default=None,
        description="S3 endpoint URL, usually for local/MinIO (env: S3_ENDPOINT_URL)",
    )
    s3_region: str = Field(
        default="us-east-1",
        description="AWS/S3 region (env: S3_REGION)",
    )
    s3_access_key_id: str | None = Field(
        default=None,
        description="S3 Access Key ID (env: S3_ACCESS_KEY_ID)",
    )
    s3_secret_access_key: str | None = Field(
        default=None,
        description="S3 Secret Access Key (env: S3_SECRET_ACCESS_KEY)",
    )
    archive_hot_retention_days: int = Field(
        default=30,
        gt=0,
        description="Number of days to keep data in hot storage before archiving (env: ARCHIVE_HOT_RETENTION_DAYS)",
    )
    archive_lateness_grace_hours: int = Field(
        default=2,
        ge=0,
        description="Grace period for late arriving logs before archiving (env: ARCHIVE_LATENESS_GRACE_HOURS)",
    )
    staging_row_limit: int = Field(
        default=100000,
        description="Maximum number of rows allowed during rehydration (env: ARCHIVE_STAGING_ROW_LIMIT)",
    )


def get_archive_settings() -> ArchiveSettings:
    """Construct Archive settings from the current environment."""
    return ArchiveSettings(
        s3_bucket_name=os.getenv("S3_BUCKET_NAME", "logsentinel-archive"),
        s3_backup_bucket=os.getenv("S3_BACKUP_BUCKET"),
        s3_endpoint_url=os.getenv("S3_ENDPOINT_URL"),
        s3_region=os.getenv("S3_REGION", "us-east-1"),
        s3_access_key_id=os.getenv("S3_ACCESS_KEY_ID"),
        s3_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY"),
        archive_hot_retention_days=int(os.getenv("ARCHIVE_HOT_RETENTION_DAYS", "30")),
        archive_lateness_grace_hours=int(
            os.getenv("ARCHIVE_LATENESS_GRACE_HOURS", "2")
        ),
        staging_row_limit=int(os.getenv("ARCHIVE_STAGING_ROW_LIMIT", "100000")),
    )


class EmailVerificationSettings(BaseModel):
    """Configuration for email verification code pipeline."""

    code_ttl_seconds: int = Field(
        default=600,
        gt=0,
        description="Time-to-live for verification codes in seconds (env: EMAIL_VERIFICATION_CODE_TTL_SECONDS)",
    )
    max_attempts: int = Field(
        default=5,
        gt=0,
        description="Maximum failed verification attempts before lockout (env: EMAIL_VERIFICATION_MAX_ATTEMPTS)",
    )
    resend_cooldown_seconds: int = Field(
        default=60,
        gt=0,
        description="Minimum seconds between verification email resends (env: EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS)",
    )
    hourly_limit: int = Field(
        default=5,
        gt=0,
        description="Maximum verification emails per email address per hour (env: EMAIL_VERIFICATION_HOURLY_LIMIT)",
    )


def get_email_verification_settings() -> EmailVerificationSettings:
    """Construct email verification settings from the current environment."""
    return EmailVerificationSettings(
        code_ttl_seconds=int(os.getenv("EMAIL_VERIFICATION_CODE_TTL_SECONDS", "600")),
        max_attempts=int(os.getenv("EMAIL_VERIFICATION_MAX_ATTEMPTS", "5")),
        resend_cooldown_seconds=int(
            os.getenv("EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS", "60")
        ),
        hourly_limit=int(os.getenv("EMAIL_VERIFICATION_HOURLY_LIMIT", "5")),
    )


class PasswordResetSettings(BaseModel):
    """Configuration for password reset token pipeline."""

    token_ttl_seconds: int = Field(
        default=900,
        gt=0,
        description="Time-to-live for password reset tokens in seconds (env: PASSWORD_RESET_TOKEN_TTL_SECONDS)",
    )


def get_password_reset_settings() -> PasswordResetSettings:
    """Construct password reset settings from the current environment."""
    return PasswordResetSettings(
        token_ttl_seconds=int(os.getenv("PASSWORD_RESET_TOKEN_TTL_SECONDS", "900")),
    )


class AuthSecuritySettings(BaseModel):
    """Security bounds for authentication processes."""

    max_concurrent_hashes_per_worker: int = Field(
        default_factory=lambda: max(1, 10 // int(os.getenv("WORKERS", os.getenv("WEB_CONCURRENCY", "1")))),
        gt=0,
        description="Maximum concurrent Argon2id hashing operations per worker process."
    )


def get_auth_security_settings() -> AuthSecuritySettings:
    """Construct auth security settings dynamically from environment variables."""
    return AuthSecuritySettings()
