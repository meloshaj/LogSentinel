"""Persistence for feature vectors and anomaly events.

Writes extracted feature windows and their anomaly predictions to
PostgreSQL, providing the data layer for historical dashboard queries.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    Table,
    and_,
    delete,
    join,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, VARCHAR
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..core.database import get_engine
from ..core.pipeline_orm import window_inputs
from ..models import FeatureVector
from ..security.tenant_boundary import TenantBoundaryViolation
from ..security.data_scope import DataScope
from ..services.durable_queue import enqueue

logger = logging.getLogger("logsentinel.feature_repository")

metadata = MetaData()

feature_windows_table = Table(
    "feature_windows",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("tenant_id", VARCHAR(64), nullable=False),
    Column("owner_user_id", BigInteger, nullable=False),
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
    Column("owner_user_id", BigInteger, nullable=False),
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
        if str(tenant_id).strip() != str(feature_vector.tenant_id).strip():
            raise TenantBoundaryViolation(
                "tenant identity mismatch at feature-persistence"
            )
        if feature_vector.owner_user_id <= 0:
            raise TenantBoundaryViolation(
                "owner identity missing at feature-persistence"
            )
        async with self.engine.begin() as conn:
            await self.persist_feature_vector_on_connection(
                conn, tenant_id, feature_vector
            )

    async def persist_feature_vector_on_connection(
        self, conn: Any, tenant_id: str, feature_vector: FeatureVector
    ) -> None:
        """Persist a feature/anomaly pair in a caller-owned transaction."""
        if str(tenant_id).strip() != str(feature_vector.tenant_id).strip():
            raise TenantBoundaryViolation(
                "tenant identity mismatch at feature-persistence"
            )
        if feature_vector.owner_user_id <= 0:
            raise TenantBoundaryViolation(
                "owner identity missing at feature-persistence"
            )
        now = datetime.now(timezone.utc)
        window_row = {
            "tenant_id": tenant_id,
            "owner_user_id": feature_vector.owner_user_id,
            "window_id": feature_vector.window_id,
            "start_time": feature_vector.window_start or now,
            "end_time": feature_vector.window_end or now,
            "service": None,
            "log_count": feature_vector.log_count,
            "feature_vector": _build_feature_json(feature_vector),
            "anomaly_prediction": feature_vector.anomaly_prediction,
            "created_at": now,
        }
        await conn.execute(
            pg_insert(feature_windows_table)
            .values(window_row)
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "owner_user_id", "window_id"]
            )
        )

        prediction = feature_vector.anomaly_prediction
        if isinstance(prediction, dict) and prediction.get("is_anomaly") is True:
            anomaly_row = {
                "tenant_id": tenant_id,
                "owner_user_id": feature_vector.owner_user_id,
                "window_id": feature_vector.window_id,
                "event_type": "anomaly.detected",
                "severity": prediction.get("severity", "unknown"),
                "score": prediction.get("anomaly_score"),
                "details": prediction,
                "acknowledged": False,
                "created_at": now,
            }
            await conn.execute(
                pg_insert(anomaly_events_table)
                .values(anomaly_row)
                .on_conflict_do_nothing(
                    index_elements=[
                        "tenant_id",
                        "owner_user_id",
                        "window_id",
                        "event_type",
                    ]
                )
            )

    async def register_feature_work(self, feature_vectors: list[FeatureVector]) -> None:
        """Register stable feature-window work before the extractor advances."""
        if not feature_vectors:
            return
        async with self.engine.begin() as conn:
            for feature_vector in feature_vectors:
                await enqueue(
                    conn,
                    tenant_id=feature_vector.tenant_id,
                    owner_user_id=feature_vector.owner_user_id,
                    topic="feature_window",
                    dedup_key=feature_vector.window_id,
                    payload=feature_vector.model_dump(mode="json"),
                    event_id=feature_vector.window_id,
                )

    async def persist_feature_input_on_connection(
        self, conn: Any, parsed_log: Any
    ) -> None:
        """Durably materialize one accepted source event for restart recovery."""
        await conn.execute(
            pg_insert(window_inputs)
            .values(
                tenant_id=parsed_log.tenant_id,
                owner_user_id=parsed_log.owner_user_id,
                event_id=parsed_log.event_id or parsed_log.id,
                event_timestamp=parsed_log.timestamp,
                payload=parsed_log.model_dump(mode="json"),
            )
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "owner_user_id", "event_id"]
            )
        )

    async def get_recent_feature_inputs(
        self, *, since: datetime, limit: int = 50000
    ) -> list[dict[str, Any]]:
        """Return a bounded active-horizon source set for process restart."""
        stmt = (
            select(window_inputs)
            .where(window_inputs.c.event_timestamp >= since)
            .order_by(
                window_inputs.c.event_timestamp.asc(), window_inputs.c.event_id.asc()
            )
            .limit(max(0, limit))
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [dict(row) for row in rows]

    async def prune_feature_inputs(self, *, before: datetime) -> int:
        """Bound durable recovery inputs once they are outside the active horizon."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                delete(window_inputs).where(window_inputs.c.event_timestamp < before)
            )
            return int(result.rowcount or 0)

    async def persist_feature_vectors(
        self, tenant_id: str, feature_vectors: list[FeatureVector]
    ) -> int:
        """Insert multiple feature vectors in a single transaction."""
        if not feature_vectors:
            return 0

        for fv in feature_vectors:
            await self.persist_feature_vector(tenant_id, fv)
        return len(feature_vectors)

    async def get_recent_features(
        self, scope: DataScope, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return recent feature windows as dicts, newest first."""
        stmt = (
            select(feature_windows_table)
            .where(
                feature_windows_table.c.tenant_id == scope.tenant_id,
                feature_windows_table.c.owner_user_id == scope.owner_user_id,
            )
            .order_by(feature_windows_table.c.created_at.desc())
            .limit(max(0, limit))
        )

        async with self.engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()

        return [dict(row) for row in rows]

    async def get_recent_anomalies(
        self, scope: DataScope, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return recent anomaly events as dicts, newest first."""
        stmt = (
            select(anomaly_events_table)
            .where(
                anomaly_events_table.c.tenant_id == scope.tenant_id,
                anomaly_events_table.c.owner_user_id == scope.owner_user_id,
            )
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
        scope: DataScope,
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
                anomaly_events_table.c.owner_user_id
                == feature_windows_table.c.owner_user_id,
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
                    anomaly_events_table.c.tenant_id == scope.tenant_id,
                    anomaly_events_table.c.owner_user_id == scope.owner_user_id,
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
