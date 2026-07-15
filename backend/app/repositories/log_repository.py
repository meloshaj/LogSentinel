"""Bulk persistence for parsed Drain3 logs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, DateTime, MetaData, Table, Text, insert
from sqlalchemy.dialects.postgresql import BIGINT, JSONB, VARCHAR
from sqlalchemy.ext.asyncio import AsyncEngine

from ..core.database import get_engine

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

    async def bulk_insert_parsed_logs(self, parsed_logs: list[dict]) -> int:
        """Insert parsed logs in a single transaction and return row count."""
        if not parsed_logs:
            return 0

        rows = [self.map_parsed_log(parsed_log) for parsed_log in parsed_logs]
        async with self.engine.begin() as connection:
            await connection.execute(insert(logs_table), rows)

        return len(rows)

    @staticmethod
    def map_parsed_log(parsed_log: dict[str, Any]) -> dict[str, Any]:
        metadata_value = parsed_log.get("metadata")
        metadata_dict = metadata_value if isinstance(metadata_value, dict) else {}

        return {
            "timestamp": _parse_datetime(metadata_dict.get("timestamp")) or datetime.now(timezone.utc),
            "service": _first_text(metadata_dict.get("service"), parsed_log.get("service"), default="unknown"),
            "raw_message": _first_text(parsed_log.get("raw_message"), default=""),
            "template_id": _first_text(parsed_log.get("template_id"), default=""),
            "template_text": _optional_text(parsed_log.get("template_text")),
            "parameters": _json_compatible(parsed_log.get("parameters"), default=[]),
            "level": _optional_text(metadata_dict.get("level") or parsed_log.get("level")),
            "source": _optional_text(metadata_dict.get("source") or parsed_log.get("source")),
            "environment": _optional_text(metadata_dict.get("environment") or parsed_log.get("environment")),
            "correlation_id": _optional_text(
                metadata_dict.get("correlation_id") or parsed_log.get("correlation_id")
            ),
            "metadata": metadata_dict,
            "parsed_at": _parse_datetime(parsed_log.get("parsed_at")),
            "created_at": datetime.now(timezone.utc),
        }


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None

    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _first_text(*values: Any, default: str) -> str:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return default


def _optional_text(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _json_compatible(value: Any, default: Any) -> Any:
    if value is None:
        return default
    return value
