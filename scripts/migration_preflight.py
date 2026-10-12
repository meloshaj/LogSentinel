"""Read-only detector for supported and unsupported historical DB shapes."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import asyncpg


CURRENT_HEAD = "20260913_0010_per_user_data_ownership"
SUPPORTED_AUTOMATIC = "SUPPORTED AUTOMATIC UPGRADE"
SUPPORTED_LEGACY = "SUPPORTED WITH APPROVED LEGACY ADOPTION"
SAFE_STOP = "SAFE-STOP — MANUAL DATA OWNERSHIP DECISION REQUIRED"
RESTORE_FORWARD_FIX = "DATABASE RESTORE / FORWARD-FIX REQUIRED"
UNSUPPORTED = "UNSUPPORTED DIRECT UPGRADE"


def classify_schema(shape: dict[str, Any]) -> dict[str, str]:
    """Classify a shape without attempting adoption or any data mutation."""

    if not shape.get("has_application_tables", False):
        return {
            "classification": SUPPORTED_AUTOMATIC,
            "action": "Run canonical bootstrap, then the allowlisted lifecycle.",
        }
    if shape.get("has_canonical_marker") and shape.get("has_owner_columns"):
        return {
            "classification": SUPPORTED_AUTOMATIC,
            "action": "Use database_lifecycle.py --validate/--apply only.",
        }
    if shape.get("has_pre_tenant_shape"):
        return {
            "classification": SAFE_STOP,
            "action": "Inventory ownership and obtain an explicit adoption decision; do not replay the frozen cutover.",
        }
    if shape.get("has_post_tenant_shape"):
        return {
            "classification": SUPPORTED_LEGACY,
            "action": "Use only the approved legacy-adoption mapping and staged, auditable forward path.",
        }
    if shape.get("has_schema_migrations"):
        return {
            "classification": RESTORE_FORWARD_FIX,
            "action": "Restore a known compatible point or obtain an approved forward-fix; do not guess a migration path.",
        }
    return {
        "classification": UNSUPPORTED,
        "action": "Stop before migration and obtain a schema/data ownership review.",
    }


def _connection_kwargs() -> dict[str, Any]:
    required = {
        "host": os.getenv("POSTGRES_HOST", "").strip(),
        "user": os.getenv("POSTGRES_USER", "").strip(),
        "database": os.getenv("POSTGRES_DB", "").strip(),
    }
    if not all(required.values()):
        raise RuntimeError("PostgreSQL connection settings are incomplete")
    ssl_mode = os.getenv("POSTGRES_SSL_MODE", "disable").strip().lower()
    return {
        **required,
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "password": os.getenv("POSTGRES_PASSWORD", ""),
        "ssl": ssl_mode != "disable",
    }


async def inspect_database() -> dict[str, Any]:
    connection = await asyncpg.connect(**_connection_kwargs())
    try:
        tables = {
            str(row["tablename"])
            for row in await connection.fetch(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                """
            )
        }
        columns = {
            str(row["column_name"])
            for row in await connection.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'logs'
                """
            )
        }
        versions = []
        if "schema_migrations" in tables:
            versions = [
                str(row["version"])
                for row in await connection.fetch(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ]
        shape: dict[str, Any] = {
            "has_application_tables": bool(tables & {"logs", "users", "tenants"}),
            "has_schema_migrations": "schema_migrations" in tables,
            "has_canonical_marker": "0000_canonical_init" in versions,
            "has_owner_columns": {"tenant_id", "owner_user_id"} <= columns,
            "has_pre_tenant_shape": "logs" in tables
            and "created_at" in columns
            and "tenant_id" not in columns,
            "has_post_tenant_shape": "logs" in tables
            and "tenant_id" in columns
            and "owner_user_id" not in columns,
            "migration_head": versions[-1] if versions else None,
            "table_count": len(tables),
            "logs_columns": sorted(columns),
        }
        return {**shape, **classify_schema(shape)}
    finally:
        await connection.close()


async def _run() -> dict[str, Any]:
    return await inspect_database()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit JSON (the default; retained for explicit runbook commands)",
    )
    parser.parse_args()
    try:
        payload = asyncio.run(_run())
    except (OSError, ValueError, asyncpg.PostgresError, RuntimeError):
        print(
            "Migration preflight failed: read-only schema inspection unavailable",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
