"""Bulk persistence for parsed Drain3 logs."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, DateTime, MetaData, Table, Text, and_, insert, select
from sqlalchemy.dialects.postgresql import BIGINT, JSONB, VARCHAR
from sqlalchemy.ext.asyncio import AsyncEngine

from ..core.database import get_engine
from ..models import ParsedLog

metadata = MetaData()

logs_table = Table(
    "logs",
    metadata,
    Column("id", BIGINT, primary_key=True, autoincrement=True),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("service", VARCHAR(255), nullable=False),
    Column("raw_message", Text, nullable=False),
    Column("template_id", VARCHAR(64), nullable=False),
    Column("template_text", Text, nullable=True),
    Column("parameters", JSONB, nullable=False),
    Column("level", VARCHAR(32), nullable=True),
    Column("source", VARCHAR(255), nullable=True),
    Column("environment", VARCHAR(255), nullable=True),
    Column("correlation_id", VARCHAR(128), nullable=True),
    Column("metadata", JSONB, nullable=False),
    Column("parsed_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
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

    async def get_recent_correlation_evidence(
        self,
        *,
        start_time: datetime,
        end_time: datetime,
        services: Sequence[str] | None = None,
        correlation_ids: Sequence[str] | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        """Return bounded recent log rows needed for service/trace evidence."""
        conditions = [
            logs_table.c.timestamp >= start_time,
            logs_table.c.timestamp <= end_time,
        ]
        cleaned_services = sorted({service for service in services or [] if service})
        cleaned_correlation_ids = sorted(
            {correlation_id for correlation_id in correlation_ids or [] if correlation_id}
        )
        if cleaned_correlation_ids:
            conditions.append(logs_table.c.correlation_id.in_(cleaned_correlation_ids))
            if cleaned_services:
                conditions.append(logs_table.c.service.in_(cleaned_services))
        elif cleaned_services:
            conditions.append(logs_table.c.service.in_(cleaned_services))

        stmt = (
            select(
                logs_table.c.timestamp,
                logs_table.c.service,
                logs_table.c.level,
                logs_table.c.correlation_id,
                logs_table.c.metadata,
            )
            .where(and_(*conditions))
            .order_by(logs_table.c.timestamp.desc(), logs_table.c.service.asc())
            .limit(max(0, limit))
        )

        async with self.engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()

        return [dict(row) for row in rows]

    @staticmethod
    def map_parsed_log(parsed_log: ParsedLog) -> dict[str, Any]:
        """Convert a validated ParsedLog into one database insert row."""
        return {
            "timestamp": parsed_log.timestamp,
            "service": getattr(parsed_log, "service_name", parsed_log.service),
            "raw_message": getattr(parsed_log, "message", getattr(parsed_log, "raw", parsed_log.raw_message)),
            "template_id": parsed_log.template_id if parsed_log.template_id else "UNPARSED_0000",
            "template_text": parsed_log.template_text,
            "parameters": [dict(item) for item in parsed_log.parameters],
            "level": parsed_log.level,
            "source": parsed_log.source,
            "environment": parsed_log.environment,
            "correlation_id": getattr(parsed_log, "correlation_id", None),
            "metadata": _json_safe_dict(parsed_log.metadata),
            "parsed_at": parsed_log.parsed_at,
            "created_at": datetime.now(timezone.utc),
        }


def _json_safe_dict(values: dict[str, Any]) -> dict[str, Any]:
    serialized: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, datetime):
            serialized[key] = value.astimezone(timezone.utc).isoformat().replace(
                "+00:00",
                "Z",
            )
        elif isinstance(value, dict):
            serialized[key] = _json_safe_dict(value)
        elif isinstance(value, list):
            serialized[key] = [
                item.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                if isinstance(item, datetime)
                else item
                for item in value
            ]
        else:
            serialized[key] = value
    return serialized
