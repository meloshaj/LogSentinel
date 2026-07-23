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
)
from sqlalchemy.dialects.postgresql import VARCHAR
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
    Column("status", VARCHAR(32), nullable=False, server_default="'triggered'"),
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
        self, window_id: str, anomaly_score: float, status: str = "triggered"
    ) -> None:
        """Insert a tracking loop record."""
        now = datetime.now(timezone.utc)

        row = {
            "window_id": window_id,
            "anomaly_score": anomaly_score,
            "status": status,
            "created_at": now,
            "updated_at": now,
        }

        try:
            async with self.engine.begin() as conn:
                await conn.execute(insert(tracking_loops_table), [row])
            logger.info("Successfully persisted tracking loop for window_id=%s with score=%.3f", window_id, anomaly_score)
        except Exception:
            logger.exception("Failed to persist tracking loop for window_id=%s", window_id)
