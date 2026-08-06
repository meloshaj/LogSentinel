"""Bulk persistence for parsed Drain3 logs."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone, timedelta
import logging
from typing import Any

import json
from sqlalchemy import Column, DateTime, MetaData, Table, Text, and_, insert, select, delete
from sqlalchemy.dialects.postgresql import BIGINT, JSONB, VARCHAR
from sqlalchemy.ext.asyncio import AsyncEngine

from ..core.database import get_engine
from ..models import ParsedLog

metadata = MetaData()

logs_table = Table(
    "logs",
    metadata,
    Column("id", VARCHAR(26), primary_key=True),
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
    Column("created_at", DateTime(timezone=True), primary_key=True, nullable=False),
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

    def _partition_log_batch(self, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Partition logs into live (>= 2 days old) and late (< 2 days old) to avoid uncompressing chunks."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=2)
        live_logs = []
        late_logs = []
        for row in rows:
            log_time = row["created_at"]
            
            # Normalize to UTC aware datetime
            if isinstance(log_time, str):
                log_time = datetime.fromisoformat(log_time.replace("Z", "+00:00"))
            if log_time.tzinfo is None:
                log_time = log_time.replace(tzinfo=timezone.utc)
            else:
                log_time = log_time.astimezone(timezone.utc)
                
            if log_time >= cutoff:
                live_logs.append(row)
            else:
                late_logs.append(row)
        return live_logs, late_logs

    async def bulk_insert_parsed_logs(self, parsed_logs: Sequence[ParsedLog]) -> int:
        """Insert parsed logs in a single transaction and return row count."""
        if not parsed_logs:
            return 0

        rows = [self.map_parsed_log(parsed_log) for parsed_log in parsed_logs]
        live_logs, late_logs = self._partition_log_batch(rows)
        
        async with self.engine.connect() as connection:
            if live_logs:
                # Extract underlying asyncpg connection for maximum throughput COPY operation
                raw_conn = await connection.get_raw_connection()
                asyncpg_conn = raw_conn.driver_connection
                
                tuples = [
                    (
                        row["id"], row["timestamp"], row["service"], row["raw_message"],
                        row["template_id"], row["template_text"], json.dumps(row["parameters"]),
                        row["level"], row["source"], row["environment"], row["correlation_id"],
                        json.dumps(row["metadata"]), row["parsed_at"], row["created_at"]
                    )
                    for row in live_logs
                ]
                
                await asyncpg_conn.copy_records_to_table(
                    "logs",
                    records=tuples,
                    columns=[
                        "id", "timestamp", "service", "raw_message", "template_id", 
                        "template_text", "parameters", "level", "source", "environment", 
                        "correlation_id", "metadata", "parsed_at", "created_at"
                    ]
                )
            
            if late_logs:
                logging.warning(
                    f"Intercepted {len(late_logs)} late-arriving logs (>2 days old). "
                    "Routing via isolated INSERT path to prevent chunk decompression lock."
                )
                SUB_BATCH_SIZE = 500
                for i in range(0, len(late_logs), SUB_BATCH_SIZE):
                    sub_batch = late_logs[i : i + SUB_BATCH_SIZE]
                    stmt = insert(logs_table).values(sub_batch)
                    await connection.execute(stmt)
            
            await connection.commit()

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
            # Mandatory chunk-exclusion filter for TimescaleDB
            logs_table.c.created_at >= start_time,
            logs_table.c.created_at <= end_time,
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
                logs_table.c.id,
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

    async def get_log_by_id(
        self, log_id: str, created_at_start: datetime, created_at_end: datetime
    ) -> dict[str, Any] | None:
        """Fetch a single log by ID with mandatory time bounds for chunk exclusion."""
        stmt = (
            select(logs_table)
            .where(
                and_(
                    logs_table.c.id == log_id,
                    logs_table.c.created_at >= created_at_start,
                    logs_table.c.created_at <= created_at_end,
                )
            )
        )
        async with self.engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.mappings().first()
            return dict(row) if row else None

    async def delete_log(
        self, log_id: str, created_at_start: datetime, created_at_end: datetime
    ) -> bool:
        """Delete a single log by ID with mandatory time bounds for chunk exclusion."""
        stmt = (
            delete(logs_table)
            .where(
                and_(
                    logs_table.c.id == log_id,
                    logs_table.c.created_at >= created_at_start,
                    logs_table.c.created_at <= created_at_end,
                )
            )
        )
        async with self.engine.begin() as conn:
            result = await conn.execute(stmt)
            return result.rowcount > 0

    @staticmethod
    def map_parsed_log(parsed_log: ParsedLog) -> dict[str, Any]:
        """Convert a validated ParsedLog into one database insert row."""
        return {
            "id": parsed_log.id,
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
            # Assign created_at to timestamp if historical, else now
            "created_at": parsed_log.timestamp if getattr(parsed_log, "timestamp", None) else datetime.now(timezone.utc),
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
