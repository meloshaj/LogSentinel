"""SQLAlchemy ORM table models for LogSentinel persistence.

Defines declarative ORM models for:
- ``LogRecord``           — parsed log events (maps existing ``logs`` table)
- ``FeatureWindowRecord`` — feature vectors extracted from sliding windows
- ``AnomalyEventRecord``  — anomaly detections linked to feature windows

All models use ``CREATE TABLE IF NOT EXISTS`` semantics via the shared
``Base.metadata`` so they can be safely applied on every application startup.
"""

from __future__ import annotations

from datetime import datetime, timezone

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


class Base(DeclarativeBase):
    """Shared declarative base for all LogSentinel ORM models."""

    pass


# ---------------------------------------------------------------------------
# Logs — maps the existing ``logs`` table created by init.sql
# ---------------------------------------------------------------------------


class LogRecord(Base):
    """ORM model for the ``logs`` table.

    Mirrors the schema defined in ``scripts/init.sql`` and the Core-SQL
    ``Table`` object previously declared in ``log_repository.py``.
    """

    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
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
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
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
    hashed_password: Mapped[str] = mapped_column(VARCHAR(255), nullable=False)
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
    status: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, default="triggered")
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
