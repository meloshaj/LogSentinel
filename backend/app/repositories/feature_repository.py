"""Persistence for feature vectors and anomaly events.

Writes extracted feature windows and their anomaly predictions to
PostgreSQL, providing the data layer for historical dashboard queries.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    Table,
    and_,
    insert,
    join,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, VARCHAR
from sqlalchemy.ext.asyncio import AsyncEngine

from ..core.database import get_engine
from ..models import FeatureVector

logger = logging.getLogger("logsentinel.feature_repository")

metadata = MetaData()

feature_windows_table = Table(
    "feature_windows",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("tenant_id", VARCHAR(64), nullable=False),
    Column("window_id", VARCHAR(128), nullable=False),
    Column("start_time", DateTime(timezone=True), nullable=False),
    Column("end_time", DateTime(timezone=True), nullable=False),
    Column("service", VARCHAR(255), nullable=True),
    Column("log_count", Integer, nullable=False, server_default="0"),
    Column("feature_vector", JSONB, nullable=False, server_default="'{}'"),
    Column("anomaly_prediction", JSONB, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

anomaly_events_table = Table(
    "anomaly_events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("tenant_id", VARCHAR(64), nullable=False),
    Column("window_id", VARCHAR(128), nullable=False),
    Column("event_type", VARCHAR(64), nullable=False),
    Column("severity", VARCHAR(32), nullable=False),
    Column("score", Float, nullable=True),
    Column("details", JSONB, nullable=True),
    Column("acknowledged", Boolean, nullable=False, server_default="false"),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


class FeatureRepository:
    """Repository for persisting feature vectors and anomaly events."""

    def __init__(self, engine: AsyncEngine | None = None) -> None:
        self._engine = engine

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is not None:
            return self._engine
        return get_engine()

    async def persist_feature_vector(
        self, tenant_id: str, feature_vector: FeatureVector
    ) -> None:
        """Insert a single feature vector and its anomaly event (if any)."""
        now = datetime.now(timezone.utc)

        window_row = {
            "tenant_id": tenant_id,
            "window_id": feature_vector.window_id,
            "start_time": feature_vector.window_start or now,
            "end_time": feature_vector.window_end or now,
            "service": None,
            "log_count": feature_vector.log_count,
            "feature_vector": _build_feature_json(feature_vector),
            "anomaly_prediction": feature_vector.anomaly_prediction,
            "created_at": now,
        }

        try:
            async with self.engine.begin() as conn:
                await conn.execute(insert(feature_windows_table), [window_row])

                # If an anomaly was detected, also write an anomaly event row
                prediction = feature_vector.anomaly_prediction
                if (
                    isinstance(prediction, dict)
                    and prediction.get("is_anomaly") is True
                ):
                    anomaly_row = {
                        "tenant_id": tenant_id,
                        "window_id": feature_vector.window_id,
                        "event_type": "anomaly.detected",
                        "severity": prediction.get("severity", "unknown"),
                        "score": prediction.get("anomaly_score"),
                        "details": prediction,
                        "acknowledged": False,
                        "created_at": now,
                    }
                    await conn.execute(insert(anomaly_events_table), [anomaly_row])

        except Exception:
            logger.exception(
                "Failed to persist feature vector %s", feature_vector.window_id
            )

    async def persist_feature_vectors(
        self, tenant_id: str, feature_vectors: list[FeatureVector]
    ) -> int:
        """Insert multiple feature vectors in a single transaction."""
        if not feature_vectors:
            return 0

        persisted = 0
        for fv in feature_vectors:
            try:
                await self.persist_feature_vector(tenant_id, fv)
                persisted += 1
            except Exception:
                logger.exception("Failed to persist feature vector %s", fv.window_id)

        return persisted

    async def get_recent_features(
        self, tenant_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return recent feature windows as dicts, newest first."""
        stmt = (
            select(feature_windows_table)
            .where(feature_windows_table.c.tenant_id == tenant_id)
            .order_by(feature_windows_table.c.created_at.desc())
            .limit(max(0, limit))
        )

        async with self.engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()

        return [dict(row) for row in rows]

    async def get_recent_anomalies(
        self, tenant_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return recent anomaly events as dicts, newest first."""
        stmt = (
            select(anomaly_events_table)
            .where(anomaly_events_table.c.tenant_id == tenant_id)
            .order_by(anomaly_events_table.c.created_at.desc())
            .limit(max(0, limit))
        )

        async with self.engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()

        return [dict(row) for row in rows]

    async def get_recent_anomaly_contexts(
        self,
        *,
        tenant_id: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return bounded anomaly events with their originating feature windows."""
        joined = join(
            anomaly_events_table,
            feature_windows_table,
            and_(
                anomaly_events_table.c.tenant_id == feature_windows_table.c.tenant_id,
                anomaly_events_table.c.window_id == feature_windows_table.c.window_id,
            ),
        )
        stmt = (
            select(
                anomaly_events_table.c.id.label("anomaly_event_id"),
                anomaly_events_table.c.window_id,
                anomaly_events_table.c.event_type,
                anomaly_events_table.c.severity,
                anomaly_events_table.c.score,
                anomaly_events_table.c.details,
                anomaly_events_table.c.created_at.label("anomaly_created_at"),
                feature_windows_table.c.start_time,
                feature_windows_table.c.end_time,
                feature_windows_table.c.service,
                feature_windows_table.c.log_count,
                feature_windows_table.c.feature_vector,
                feature_windows_table.c.anomaly_prediction,
            )
            .select_from(joined)
            .where(
                and_(
                    anomaly_events_table.c.tenant_id == tenant_id,
                    anomaly_events_table.c.created_at >= start_time,
                    anomaly_events_table.c.created_at <= end_time,
                )
            )
            .order_by(
                anomaly_events_table.c.created_at.desc(),
                anomaly_events_table.c.window_id.asc(),
            )
            .limit(max(0, limit))
        )

        async with self.engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()

        return [dict(row) for row in rows]


def _build_feature_json(fv: FeatureVector) -> dict[str, Any]:
    """Build the JSONB payload for the feature_vector column."""
    base: dict[str, Any] = {
        "log_count": fv.log_count,
        "unique_templates": fv.unique_templates,
        "error_count": fv.error_count,
        "warning_count": fv.warning_count,
        "logs_per_second": fv.logs_per_second,
        "template_entropy": fv.template_entropy,
        "template_frequencies": fv.template_frequencies,
        "service_distribution": fv.service_distribution,
    }

    # Merge any extra features from the features dict
    if fv.features:
        base.update(fv.features)

    return base
