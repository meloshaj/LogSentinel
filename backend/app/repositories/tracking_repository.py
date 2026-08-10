"""Persistence for tracking infrastructure loops.

Writes tracking loop records to PostgreSQL when anomaly thresholds are met.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    Table,
    insert,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, VARCHAR
from sqlalchemy.ext.asyncio import AsyncEngine

from ..core.database import get_engine

logger = logging.getLogger("logsentinel.tracking_repository")

metadata = MetaData()

tracking_loops_table = Table(
    "tracking_loops",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("window_id", VARCHAR(128), nullable=False),
    Column("anomaly_score", Float, nullable=False),
    Column("status", VARCHAR(32), nullable=False, server_default="'ACTIVE'"),
    Column("blast_radius", JSONB, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
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
        window_id: str,
        anomaly_score: float,
        status: str = "ACTIVE",
        blast_radius: dict | None = None,
    ) -> None:
        """Insert a tracking loop record."""
        now = datetime.now(timezone.utc)

        row = {
            "window_id": window_id,
            "anomaly_score": anomaly_score,
            "status": status,
            "blast_radius": blast_radius,
            "created_at": now,
            "updated_at": now,
        }

        try:
            async with self.engine.begin() as conn:
                await conn.execute(insert(tracking_loops_table), [row])
            logger.info("Successfully persisted tracking loop for window_id=%s with score=%.3f", window_id, anomaly_score)
        except Exception:
            logger.exception("Failed to persist tracking loop for window_id=%s", window_id)

    async def get_tracking_loop_by_id(self, tracking_loop_id: int) -> dict | None:
        """Return one tracking-loop row by primary key without mutating it."""
        stmt = (
            select(tracking_loops_table)
            .where(tracking_loops_table.c.id == tracking_loop_id)
            .limit(1)
        )

        async with self.engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.mappings().first()

        return dict(row) if row is not None else None

    async def get_active_tracking_loops(self, limit: int = 100) -> list[dict]:
        """Return all tracking loops with ACTIVE status, newest first."""
        stmt = (
            select(tracking_loops_table)
            .where(tracking_loops_table.c.status == "ACTIVE")
            .order_by(tracking_loops_table.c.created_at.desc())
            .limit(limit)
        )

        async with self.engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()

        return [dict(row) for row in rows]
