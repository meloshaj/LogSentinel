"""Bulk persistence for parsed Drain3 logs."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import (
    and_,
    delete,
    insert,
    select,
)
from sqlalchemy.ext.asyncio import AsyncEngine

from ..core.database import get_engine
from ..core.orm import LogRecord
from ..models import ParsedLog

logs_table = LogRecord.__table__


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

    @staticmethod
    def _serialize_for_copy(row: dict[str, Any]) -> tuple:
        """Normalize a row dict into a tuple strictly typed for ``asyncpg.copy_records_to_table``.

        Each field is coerced to its binary COPY-compatible type:
        - ``parameters`` / ``metadata``: ``json.dumps()`` (str), with safe defaults.
        - ``source``, ``environment``: fallback to ``"unknown"``/``"production"`` if ``None``.
        - ``parsed_at``: defaults to ``datetime.now(UTC)`` if ``None``.
        - All other fields: passed through as-is (str / datetime).
        """
        parameters = row.get("parameters")
        if isinstance(parameters, (dict, list)):
            parameters = json.dumps(parameters)
        elif parameters is None:
            parameters = "[]"

        metadata = row.get("metadata")
        if isinstance(metadata, dict):
            metadata = json.dumps(metadata)
        elif metadata is None:
            metadata = "{}"

        return (
            row["id"],
            row["timestamp"],
            row["service"],
            row["raw_message"],
            row["template_id"],
            row.get("template_text"),
            parameters,
            row.get("level"),
            row.get("source") or "unknown",
            row.get("environment") or "production",
            row.get("correlation_id"),
            metadata,
            row.get("parsed_at") or datetime.now(timezone.utc),
            row["created_at"],
        )

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
                
                tuples = [self._serialize_for_copy(row) for row in live_logs]
                
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
                    stmt = insert(logs_table)
                    await connection.execute(stmt, sub_batch)
            
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
                logs_table.c.metadata_,
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

    async def get_recent_logs(self, limit: int = 500) -> list[dict[str, Any]]:
        """Return the most recent logs for backfilling the UI."""
        stmt = (
            select(
                logs_table.c.id,
                logs_table.c.timestamp,
                logs_table.c.service,
                logs_table.c.raw_message,
                logs_table.c.level,
                logs_table.c.template_id,
                logs_table.c.template_text,
                logs_table.c.metadata_,
            )
            .order_by(logs_table.c.created_at.desc())
            .limit(limit)
        )

        async with self.engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()

        return [dict(row) for row in rows]

    async def get_logs_paginated(
        self, page: int = 1, limit: int = 50, service: str | None = None, level: str | None = None
    ) -> dict[str, Any]:
        """Fetch paginated logs with optional filters."""
        from sqlalchemy import func
        conditions = []
        if service:
            conditions.append(logs_table.c.service == service)
        if level:
            conditions.append(logs_table.c.level == level)
        
        where_clause = and_(*conditions) if conditions else True

        count_stmt = select(func.count()).select_from(logs_table).where(where_clause)
        
        stmt = (
            select(
                logs_table.c.id,
                logs_table.c.timestamp,
                logs_table.c.service,
                logs_table.c.raw_message,
                logs_table.c.level,
                logs_table.c.template_id,
                logs_table.c.template_text,
                logs_table.c.metadata,
            )
            .where(where_clause)
            .order_by(logs_table.c.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )

        async with self.engine.connect() as conn:
            total_count = await conn.scalar(count_stmt) or 0
            result = await conn.execute(stmt)
            rows = result.mappings().all()

        items = [dict(row) for row in rows]
        pages = (total_count + limit - 1) // limit if limit > 0 else 0

        return {
            "items": items,
            "total": total_count,
            "page": page,
            "limit": limit,
            "pages": pages
        }

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
