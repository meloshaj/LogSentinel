"""Ordinary PostgreSQL queue tables; these must never become hypertables."""

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
ledger = Table(
    "pipeline_ledger",
    metadata,
    Column("tenant_id", String(64), primary_key=True),
    Column("owner_user_id", Integer, primary_key=True),
    Column("stage", String(32), primary_key=True),
    Column("event_id", String(128), primary_key=True),
    Column(
        "created_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
)
outbox = Table(
    "pipeline_outbox",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("owner_user_id", Integer, nullable=True),
    Column("topic", String(32), nullable=False),
    Column("dedup_key", String(128), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("status", String(16), nullable=False, server_default="pending"),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column(
        "available_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    Column(
        "created_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
    Column(
        "updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
    Column("locked_at", DateTime(timezone=True)),
    Column("locked_by", String(128)),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("delivery_type", String(32), nullable=False, server_default="generic"),
    Column("destination_id", String(128)),
    Column("event_id", String(128)),
    Column("incident_id", String(128)),
    Column("delivered_at", DateTime(timezone=True)),
    Column("last_error_category", String(64)),
    Column("last_error", String(128)),
    UniqueConstraint("tenant_id", "owner_user_id", "topic", "dedup_key"),
    CheckConstraint(
        "status IN ('pending', 'retry', 'processing', 'completed', 'delivered', 'failed', 'done', 'dead')"
    ),
    CheckConstraint("attempts >= 0"),
)
Index(
    "ix_pipeline_outbox_ready", outbox.c.topic, outbox.c.status, outbox.c.available_at
)
window_inputs = Table(
    "pipeline_feature_inputs",
    metadata,
    Column("tenant_id", String(64), primary_key=True),
    Column("owner_user_id", Integer, primary_key=True),
    Column("event_id", String(128), primary_key=True),
    Column("event_timestamp", DateTime(timezone=True), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column(
        "created_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
)
Index(
    "ix_pipeline_feature_inputs_timestamp",
    window_inputs.c.tenant_id,
    window_inputs.c.owner_user_id,
    window_inputs.c.event_timestamp,
)
