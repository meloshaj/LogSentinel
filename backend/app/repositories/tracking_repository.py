"""Persistence for tracking infrastructure loops.

Writes tracking loop records to PostgreSQL when anomaly thresholds are met.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    Table,
    and_,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, VARCHAR
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from ..core.database import get_engine
from ..schemas.alerting import IncidentAlertPayload
from ..security.tenant_boundary import TenantBoundaryViolation
from ..security.data_scope import DataScope
from ..services.alerting import enqueue_incident_alert

logger = logging.getLogger("logsentinel.tracking_repository")

metadata = MetaData()

tracking_loops_table = Table(
    "tracking_loops",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("tenant_id", VARCHAR(64), nullable=False),
    Column("owner_user_id", BigInteger, nullable=False),
    Column("window_id", VARCHAR(128), nullable=False),
    Column("anomaly_score", Float, nullable=False),
    Column("status", VARCHAR(32), nullable=False, server_default="'ACTIVE'"),
    Column("blast_radius", JSONB, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

incident_triage_history_table = Table(
    "incident_triage_history",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("tenant_id", VARCHAR(64), nullable=False),
    Column("tracking_loop_id", Integer, nullable=False),
    Column("actor_user_id", Integer, nullable=False),
    Column("previous_status", VARCHAR(32), nullable=False),
    Column("new_status", VARCHAR(32), nullable=False),
    Column("note", VARCHAR(1000), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


class TrackingRepository:
    """Repository for persisting automated tracking loops."""

    def __init__(self, engine: AsyncEngine | None = None) -> None:
        self._engine = engine

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is not None:
            return self._engine
        return get_engine()

    async def persist_tracking_loop(
        self,
        tenant_id: str,
        owner_user_id: int,
        window_id: str,
        anomaly_score: float,
        status: str = "ACTIVE",
        blast_radius: dict | None = None,
        alert_payload: IncidentAlertPayload | None = None,
    ) -> bool:
        """Insert one tenant/window incident, returning whether it was new."""
        async with self.engine.begin() as conn:
            return await self.persist_tracking_loop_on_connection(
                conn,
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                window_id=window_id,
                anomaly_score=anomaly_score,
                status=status,
                blast_radius=blast_radius,
                alert_payload=alert_payload,
            )

    async def persist_tracking_loop_on_connection(
        self,
        conn,
        *,
        tenant_id: str,
        owner_user_id: int,
        window_id: str,
        anomaly_score: float,
        status: str = "ACTIVE",
        blast_radius: dict | None = None,
        alert_payload: IncidentAlertPayload | None = None,
    ) -> bool:
        """Persist an incident and alert acceptance in a caller transaction."""
        if alert_payload is not None and (
            str(alert_payload.tenant_id).strip() != str(tenant_id).strip()
        ):
            raise TenantBoundaryViolation(
                "tenant identity mismatch at incident-persistence"
            )
        if owner_user_id <= 0 or (
            alert_payload is not None and alert_payload.owner_user_id != owner_user_id
        ):
            raise TenantBoundaryViolation(
                "owner identity mismatch at incident-persistence"
            )
        now = datetime.now(timezone.utc)

        row = {
            "tenant_id": tenant_id,
            "owner_user_id": owner_user_id,
            "window_id": window_id,
            "anomaly_score": anomaly_score,
            "status": status,
            "blast_radius": blast_radius,
            "created_at": now,
            "updated_at": now,
        }

        inserted = await conn.execute(
            pg_insert(tracking_loops_table)
            .values(row)
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "owner_user_id", "window_id"]
            )
            .returning(tracking_loops_table.c.id)
        )
        created = inserted.scalar_one_or_none() is not None
        if alert_payload is not None:
            await enqueue_incident_alert(conn, alert_payload)
        logger.info(
            "Successfully persisted tracking loop for window_id=%s with score=%.3f",
            window_id,
            anomaly_score,
        )
        return created

    async def get_tracking_loop_by_id(
        self, scope: DataScope, tracking_loop_id: int
    ) -> dict | None:
        """Return one tracking-loop row by primary key without mutating it."""
        stmt = (
            select(tracking_loops_table)
            .where(
                and_(
                    tracking_loops_table.c.tenant_id == scope.tenant_id,
                    tracking_loops_table.c.owner_user_id == scope.owner_user_id,
                    tracking_loops_table.c.id == tracking_loop_id,
                )
            )
            .limit(1)
        )

        async with self.engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.mappings().first()

        return dict(row) if row is not None else None

    async def get_active_tracking_loops(
        self, scope: DataScope, limit: int = 100
    ) -> list[dict]:
        """Return durable incident history, newest first (legacy method name)."""
        stmt = (
            select(tracking_loops_table)
            .where(
                tracking_loops_table.c.tenant_id == scope.tenant_id,
                tracking_loops_table.c.owner_user_id == scope.owner_user_id,
            )
            .order_by(tracking_loops_table.c.created_at.desc())
            .limit(limit)
        )

        async with self.engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()

        return [dict(row) for row in rows]

    async def get_incident_detail(
        self, scope: DataScope, tracking_loop_id: int
    ) -> dict | None:
        row = await self.get_tracking_loop_by_id(scope, tracking_loop_id)
        if row is None:
            return None
        stmt = (
            select(incident_triage_history_table)
            .where(
                incident_triage_history_table.c.tenant_id == scope.tenant_id,
                incident_triage_history_table.c.tracking_loop_id == tracking_loop_id,
            )
            .order_by(incident_triage_history_table.c.created_at.asc())
        )
        async with self.engine.connect() as conn:
            history = (await conn.execute(stmt)).mappings().all()
        row["history"] = [dict(item) for item in history]
        return row

    async def update_incident_status(
        self,
        *,
        scope: DataScope,
        tracking_loop_id: int,
        actor_user_id: int,
        new_status: str,
        note: str | None = None,
    ) -> dict | None:
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as conn:
            current_result = await conn.execute(
                select(tracking_loops_table)
                .where(
                    tracking_loops_table.c.tenant_id == scope.tenant_id,
                    tracking_loops_table.c.owner_user_id == scope.owner_user_id,
                    tracking_loops_table.c.id == tracking_loop_id,
                )
                .with_for_update()
            )
            current = current_result.mappings().first()
            if current is None:
                return None
            previous = str(current["status"]).lower()
            await conn.execute(
                update(tracking_loops_table)
                .where(
                    tracking_loops_table.c.tenant_id == scope.tenant_id,
                    tracking_loops_table.c.owner_user_id == scope.owner_user_id,
                    tracking_loops_table.c.id == tracking_loop_id,
                )
                .values(status=new_status, updated_at=now)
            )
            await conn.execute(
                insert(incident_triage_history_table),
                [
                    {
                        "tenant_id": scope.tenant_id,
                        "tracking_loop_id": tracking_loop_id,
                        "actor_user_id": actor_user_id,
                        "previous_status": previous,
                        "new_status": new_status,
                        "note": note,
                        "created_at": now,
                    }
                ],
            )
        return await self.get_incident_detail(scope, tracking_loop_id)
