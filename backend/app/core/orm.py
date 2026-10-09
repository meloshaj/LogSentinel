from __future__ import annotations

import os
from datetime import datetime, timezone

from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, VARCHAR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

# Ensure key is a valid Fernet key. It must be 32 URL-safe base64-encoded bytes.
# There is intentionally no development fallback: importing persistence models
# without the deployment-managed key must fail closed.
_secret = os.getenv("ENCRYPTION_KEY")
if not _secret:
    raise ValueError("ENCRYPTION_KEY environment variable is not set")
_current_fernet = Fernet(_secret.encode("utf-8"))
_previous_secret = os.getenv("ENCRYPTION_KEY_PREVIOUS", "").strip()
_decrypt_fernet = MultiFernet(
    [_current_fernet]
    + ([Fernet(_previous_secret.encode("utf-8"))] if _previous_secret else [])
)


def rotate_encrypted_value(value: str) -> str:
    """Decrypt with the current/previous key and re-encrypt with current."""
    try:
        plaintext = _decrypt_fernet.decrypt(value.encode("utf-8"))
    except Exception as exc:
        raise ValueError("encrypted database value could not be decrypted") from exc
    return _current_fernet.encrypt(plaintext).decode("utf-8")


class EncryptedString(TypeDecorator):
    """Transparently encrypt/decrypt strings using Fernet."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            return _current_fernet.encrypt(value.encode("utf-8")).decode("utf-8")
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            try:
                return _decrypt_fernet.decrypt(value.encode("utf-8")).decode("utf-8")
            except Exception as exc:
                # Ciphertext/key failures are integrity failures. Returning the
                # stored value would silently turn a key rotation or corrupt
                # token into an apparent successful read.
                raise ValueError(
                    "encrypted database value could not be decrypted"
                ) from exc
        return value


class Base(DeclarativeBase):
    """Shared declarative base for all LogSentinel ORM models."""


# ---------------------------------------------------------------------------
# Logs — maps the existing ``logs`` table created by init.sql
# ---------------------------------------------------------------------------


class LogRecord(Base):
    """ORM model for the ``logs`` table.

    Mirrors the schema defined in ``scripts/init.sql`` and the Core-SQL
    ``Table`` object previously declared in ``log_repository.py``.
    """

    __tablename__ = "logs"

    id: Mapped[str] = mapped_column(VARCHAR(26), primary_key=True)
    event_id: Mapped[str] = mapped_column(
        VARCHAR(128), nullable=False, default=lambda: "legacy"
    )
    tenant_id: Mapped[str] = mapped_column(
        VARCHAR(64), nullable=False, default="default", primary_key=True
    )
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    service: Mapped[str] = mapped_column(VARCHAR(255), nullable=False)
    raw_message: Mapped[str] = mapped_column(Text, nullable=False)
    template_id: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    template_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parameters: Mapped[dict | list] = mapped_column(JSONB, nullable=False, default=list)
    level: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True)
    source: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    environment: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(VARCHAR(128), nullable=True)
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    parsed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Feature Windows — extracted feature vectors from log sliding windows
# ---------------------------------------------------------------------------


class FeatureWindowRecord(Base):
    """ORM model for the ``feature_windows`` table.

    Each row represents a single sliding-window feature vector produced by
    the ``FeatureExtractionWorker`` and is the primary persistence target
    for downstream ML scoring and dashboarding.
    """

    __tablename__ = "feature_windows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        VARCHAR(64), nullable=False, default="default"
    )
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    window_id: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    service: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    log_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    feature_vector: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="Full FeatureVector dict serialized as JSON",
    )
    anomaly_prediction: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Structured anomaly detection output (scores, labels, etc.)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "owner_user_id",
            "window_id",
            name="uq_feature_windows_tenant_window_id",
        ),
    )


# ---------------------------------------------------------------------------
# Anomaly Events — individual anomaly detections linked to feature windows
# ---------------------------------------------------------------------------


class AnomalyEventRecord(Base):
    """ORM model for the ``anomaly_events`` table.

    Tracks individual anomaly events detected during feature-window scoring.
    Each event references the ``feature_windows`` row that triggered it via
    ``window_id``.
    """

    __tablename__ = "anomaly_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        VARCHAR(64), nullable=False, default="default"
    )
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    window_id: Mapped[str] = mapped_column(
        VARCHAR(128),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    severity: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "owner_user_id", "window_id"],
            [
                "feature_windows.tenant_id",
                "feature_windows.owner_user_id",
                "feature_windows.window_id",
            ],
            ondelete="CASCADE",
        ),
    )


# ---------------------------------------------------------------------------
# Users — registered user credentials and profile information
# ---------------------------------------------------------------------------


class UserRecord(Base):
    """ORM model for the ``users`` table."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(VARCHAR(255), nullable=False, unique=True)
    hashed_password: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    organization: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    tenant_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("tenants.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, default="active")
    role: Mapped[str] = mapped_column(
        VARCHAR(32), nullable=False, default="viewer", server_default="viewer"
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_verification', 'active', 'suspended')",
            name="ck_users_status",
        ),
        CheckConstraint(
            "role IN ('viewer', 'operator', 'admin')",
            name="ck_users_role",
        ),
    )


# ---------------------------------------------------------------------------
# Accounts — multi-provider OAuth accounts linked to users
# ---------------------------------------------------------------------------


class AccountRecord(Base):
    """ORM model for the ``accounts`` table to support OAuth providers."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    provider: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    provider_account_id: Mapped[str] = mapped_column(VARCHAR(512), nullable=False)
    access_token: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_account_id",
            name="uq_accounts_provider_provider_account_id",
        ),
    )


class IngestionApiKeyRecord(Base):
    """Tenant-owned machine credential; only a SHA-256 digest is persisted."""

    __tablename__ = "ingestion_api_keys"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("tenants.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    key_prefix: Mapped[str] = mapped_column(VARCHAR(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(VARCHAR(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    scopes: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        default=lambda: ["logs:ingest"],
        comment="Canonical machine permission; NULL legacy rows fail closed",
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TenantRecord(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    name: Mapped[str] = mapped_column(VARCHAR(255), nullable=False)
    status: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    __table_args__ = (
        CheckConstraint(
            "id <> 'default' AND length(trim(id)) > 0", name="ck_tenants_explicit_id"
        ),
        CheckConstraint("status IN ('active', 'suspended')", name="ck_tenants_status"),
    )


class TenantMembershipRecord(Base):
    __tablename__ = "tenant_memberships"
    tenant_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("tenants.id"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    status: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, default="active")
    __table_args__ = (
        CheckConstraint(
            "role IN ('viewer', 'operator', 'admin')", name="ck_membership_role"
        ),
        CheckConstraint(
            "status IN ('active', 'suspended')", name="ck_membership_status"
        ),
    )


class ProviderTenantMappingRecord(Base):
    __tablename__ = "provider_tenant_mappings"
    provider: Mapped[str] = mapped_column(VARCHAR(32), primary_key=True)
    issuer: Mapped[str] = mapped_column(VARCHAR(512), primary_key=True)
    provider_tenant_id: Mapped[str] = mapped_column(VARCHAR(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("tenants.id"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_role: Mapped[str] = mapped_column(
        VARCHAR(32), nullable=False, default="viewer"
    )
    __table_args__ = (
        CheckConstraint(
            "default_role IN ('viewer', 'operator')", name="ck_provider_mapping_role"
        ),
    )


class AuthSessionRecord(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("tenants.id"), nullable=False
    )
    csrf_hash: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reuse_detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RefreshTokenRecord(Base):
    __tablename__ = "auth_refresh_tokens"
    token_hash: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        VARCHAR(64),
        ForeignKey("auth_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rotated_from: Mapped[str | None] = mapped_column(VARCHAR(64))
    rotated_to: Mapped[str | None] = mapped_column(VARCHAR(64))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailOutboxRecord(Base):
    """Durable authentication email work; sensitive template data is encrypted."""

    __tablename__ = "email_outbox"
    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )
    tenant_id: Mapped[str | None] = mapped_column(
        VARCHAR(64), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    recipient: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    template_data: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        VARCHAR(128), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(VARCHAR(16), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(VARCHAR(128))
    last_error_category: Mapped[str | None] = mapped_column(VARCHAR(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasswordResetTokenRecord(Base):
    """PostgreSQL-authoritative password-reset capability lifecycle.

    ``token_digest`` is the stable operation/idempotency identity.  The raw
    token is never stored in this table; it is only carried to the encrypted
    authentication email outbox at issuance time.
    """

    __tablename__ = "password_reset_tokens"

    token_digest: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("tenants.id"), nullable=False
    )
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    state: Mapped[str] = mapped_column(
        VARCHAR(16), nullable=False, default="issued", server_default="issued"
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "state IN ('issued', 'completed', 'expired', 'invalidated')",
            name="ck_password_reset_tokens_state",
        ),
        CheckConstraint(
            "expires_at > issued_at", name="ck_password_reset_tokens_expiry"
        ),
        CheckConstraint(
            "(state = 'completed' AND completed_at IS NOT NULL) OR "
            "(state <> 'completed' AND completed_at IS NULL)",
            name="ck_password_reset_tokens_completed_state",
        ),
        CheckConstraint(
            "(state = 'expired' AND expired_at IS NOT NULL) OR "
            "(state <> 'expired' AND expired_at IS NULL)",
            name="ck_password_reset_tokens_expired_state",
        ),
        CheckConstraint(
            "(state = 'invalidated' AND invalidated_at IS NOT NULL) OR "
            "(state <> 'invalidated' AND invalidated_at IS NULL)",
            name="ck_password_reset_tokens_invalidated_state",
        ),
    )


class SecurityAuditRecord(Base):
    __tablename__ = "security_audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("tenants.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class TenantSettingsRecord(Base):
    __tablename__ = "tenant_settings"
    tenant_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class TenantIntegrationRecord(Base):
    """Encrypted, tenant-owned outbound integration destination."""

    __tablename__ = "tenant_integrations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    destination_url: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "owner_user_id",
            "provider",
            name="uq_tenant_integrations_tenant_owner_provider",
        ),
        CheckConstraint(
            "provider IN ('slack', 'discord')", name="ck_tenant_integrations_provider"
        ),
    )


# ---------------------------------------------------------------------------
# External Identities — federated provider identity mappings
# ---------------------------------------------------------------------------


class ExternalIdentityRecord(Base):
    """ORM model for the ``external_identities`` table.

    Each row represents a single verified external provider identity
    (e.g. Microsoft Entra, Google) linked to an internal LogSentinel
    user.  The stable lookup key is (provider, issuer, subject).

    The ``subject`` column stores the provider-stable identifier — for
    Microsoft this is the ``sub`` claim (audience-specific pairwise ID),
    NOT an email address.
    """

    __tablename__ = "external_identities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    issuer: Mapped[str] = mapped_column(VARCHAR(512), nullable=False)
    subject: Mapped[str] = mapped_column(VARCHAR(512), nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(VARCHAR(128), nullable=True)
    provider_object_id: Mapped[str | None] = mapped_column(
        VARCHAR(128),
        nullable=True,
        comment="Microsoft oid or provider-specific immutable object ID",
    )
    email: Mapped[str | None] = mapped_column(
        VARCHAR(255),
        nullable=True,
        comment="Contact email from the provider (informational only, not a lookup key)",
    )
    display_name: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "issuer",
            "subject",
            name="uq_external_identities_provider_issuer_subject",
        ),
    )


# ---------------------------------------------------------------------------
# Tracking Loops — automated tracking for anomaly alerts
# ---------------------------------------------------------------------------


class TrackingLoopRecord(Base):
    """ORM model for the ``tracking_loops`` table."""

    __tablename__ = "tracking_loops"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        VARCHAR(64), nullable=False, default="default"
    )
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    window_id: Mapped[str] = mapped_column(
        VARCHAR(128),
        nullable=False,
    )
    anomaly_score: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(
        VARCHAR(32), nullable=False, default="triggered"
    )
    blast_radius: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "owner_user_id",
            "window_id",
            name="uq_tracking_loops_tenant_window_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "owner_user_id", "window_id"],
            [
                "feature_windows.tenant_id",
                "feature_windows.owner_user_id",
                "feature_windows.window_id",
            ],
            ondelete="CASCADE",
        ),
    )
