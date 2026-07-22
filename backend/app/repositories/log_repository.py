"""Bulk persistence for parsed Drain3 logs."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, DateTime, MetaData, Table, Text, insert
from sqlalchemy.dialects.postgresql import BIGINT, JSONB, VARCHAR
from sqlalchemy.ext.asyncio import AsyncEngine

from ..core.database import get_engine
from ..models import ParsedLog

metadata = MetaData()

logs_table = Table(
    "logs",
    metadata,
    Column("id", BIGINT, primary_key=True),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("service", VARCHAR(255), nullable=False),
    Column("raw_message", Text, nullable=False),
    Column("template_id", VARCHAR(64), nullable=False),
    Column("template_text", Text),
    Column("parameters", JSONB),
    Column("level", VARCHAR(32)),
    Column("source", VARCHAR(255)),
    Column("environment", VARCHAR(255)),
    Column("correlation_id", VARCHAR(128)),
    Column("metadata", JSONB),
    Column("parsed_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True)),
)


class LogRepository:
    """Repository for writing parsed logs to PostgreSQL."""

    def __init__(self, engine: AsyncEngine | None = None) -> None:
        self._engine = engine

    @property
    def engine(self) -> AsyncEngine:
        """Return the injected engine or fall back to the global pool."""
        if self._engine is not None:
            return self._engine
        return get_engine()

    async def bulk_insert_parsed_logs(self, parsed_logs: Sequence[ParsedLog]) -> int:
        """Insert parsed logs in a single transaction and return row count."""
        if not parsed_logs:
            return 0

        rows = [self.map_parsed_log(parsed_log) for parsed_log in parsed_logs]
        async with self.engine.begin() as connection:
            await connection.execute(insert(logs_table), rows)

        return len(rows)

    @staticmethod
    def map_parsed_log(parsed_log: ParsedLog) -> dict[str, Any]:
        """Convert a validated ParsedLog into one database insert row.

        ``cluster_size`` and ``change_type`` remain runtime-only because the
        current logs schema has no authorized columns for those fields.
        """
        json_fields = parsed_log.model_dump(
            mode="json",
            include={"parameters", "metadata"},
        )

        return {
            "timestamp": parsed_log.timestamp,
            "service": parsed_log.service,
            "raw_message": parsed_log.raw_message,
            "template_id": parsed_log.template_id,
            "template_text": parsed_log.template_text,
            "parameters": json_fields["parameters"],
            "level": parsed_log.level,
            "source": parsed_log.source,
            "environment": parsed_log.environment,
            "correlation_id": parsed_log.correlation_id,
            "metadata": json_fields["metadata"],
            "parsed_at": parsed_log.parsed_at,
            "created_at": datetime.now(timezone.utc),
        }
