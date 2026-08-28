from __future__ import annotations

import os
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, VARCHAR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

# Ensure key is a valid Fernet key. It must be 32 URL-safe base64-encoded bytes.
_secret = os.getenv("ENCRYPTION_KEY")
if not _secret:
    raise ValueError(
        "ENCRYPTION_KEY environment variable is not set. It must be a 32-byte URL-safe base64-encoded string."
    )
_fernet = Fernet(_secret.encode("utf-8"))


class EncryptedString(TypeDecorator):
    """Transparently encrypt/decrypt strings using Fernet."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            return _fernet.encrypt(value.encode("utf-8")).decode("utf-8")
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            try:
                return _fernet.decrypt(value.encode("utf-8")).decode("utf-8")
            except Exception:
                # Fallback if decryption fails (e.g. data was plaintext)
                return value
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
    window_id: Mapped[str] = mapped_column(VARCHAR(128), nullable=False, unique=True)
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
        UniqueConstraint("window_id", name="uq_feature_windows_window_id"),
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
    window_id: Mapped[str] = mapped_column(
        VARCHAR(128),
        ForeignKey("feature_windows.window_id", ondelete="CASCADE"),
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
    window_id: Mapped[str] = mapped_column(
        VARCHAR(128),
        ForeignKey("feature_windows.window_id", ondelete="CASCADE"),
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
