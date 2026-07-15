"""Database health and schema checks for the Drain3 persistence pipeline."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ..core.database import get_engine

REQUIRED_LOG_COLUMNS = {
    "id",
    "timestamp",
    "service",
    "raw_message",
    "template_id",
    "template_text",
    "parameters",
    "level",
    "source",
    "environment",
    "correlation_id",
    "metadata",
    "parsed_at",
    "created_at",
}

LATEST_LOGS_QUERY = text(
    """
    SELECT id, service, raw_message, template_id, template_text, parameters, correlation_id, parsed_at
    FROM logs
    ORDER BY created_at DESC
    LIMIT 10
    """
)


def find_missing_columns(existing_columns: set[str]) -> list[str]:
    """Return required Drain3 log columns absent from the provided set."""
    return sorted(REQUIRED_LOG_COLUMNS - existing_columns)


async def check_database_health(engine: AsyncEngine | None = None) -> dict[str, Any]:
    """Check database connectivity and required Drain3 logs schema."""
    if engine is None:
        engine = get_engine()
    try:
        async with engine.connect() as connection:
            table_exists = await _logs_table_exists(connection)
            existing_columns = await _logs_columns(connection) if table_exists else set()
            missing_columns = find_missing_columns(existing_columns)

            return {
                "connected": True,
                "table_exists": table_exists,
                "missing_columns": missing_columns,
                "error": None,
            }
    except Exception as exc:
        return {
            "connected": False,
            "table_exists": False,
            "missing_columns": sorted(REQUIRED_LOG_COLUMNS),
            "error": f"{type(exc).__name__}: {exc}",
        }


async def fetch_latest_logs(engine: AsyncEngine | None = None) -> list[dict[str, Any]]:
    """Fetch latest parsed logs for verification output."""
    if engine is None:
        engine = get_engine()
    async with engine.connect() as connection:
        result = await connection.execute(LATEST_LOGS_QUERY)
        return [dict(row._mapping) for row in result]


async def _logs_table_exists(connection: Any) -> bool:
    result = await connection.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = 'logs'
            )
            """
        )
    )
    return bool(result.scalar_one())


async def _logs_columns(connection: Any) -> set[str]:
    result = await connection.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'logs'
            """
        )
    )
    return {str(row[0]) for row in result}
