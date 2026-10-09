#!/usr/bin/env python3
"""Plan or explicitly apply the canonical LogSentinel retention policy.

The default is a read-only plan. Destructive execution requires all of:
approved policy metadata, policy-level apply enablement, ``--apply``, and a
database advisory lock. Every database batch is a bounded transaction. The
command never runs as an application-startup side effect.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.retention.engine import (  # noqa: E402
    PolicyError,
    load_policy,
    parse_duration,
    policy_status,
)

UTC = dt.timezone.utc
LOCK_NAMESPACE = 714203


class RetentionError(RuntimeError):
    """Raised when a retention plan cannot be proven safe."""


TABLE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "class": "postgres.anomaly_events",
        "table": "anomaly_events",
        "time_column": "created_at",
        "key": "ctid",
        "extra": "",
        "order": "created_at, id",
    },
    {
        "class": "postgres.tracking_loops",
        "table": "tracking_loops",
        "time_column": "created_at",
        "key": "ctid",
        "extra": "",
        "order": "created_at, id",
    },
    {
        "class": "postgres.feature_windows",
        "table": "feature_windows",
        "time_column": "created_at",
        "key": "ctid",
        "extra": "",
        "order": "created_at, id",
    },
    {
        "class": "postgres.incidents",
        "table": "incidents",
        "time_column": "created_at",
        "key": "ctid",
        "extra": "status = 'RESOLVED'",
        "order": "created_at, id",
    },
    {
        "class": "postgres.feature_inputs",
        "table": "pipeline_feature_inputs",
        "time_column": "created_at",
        "key": "ctid",
        "extra": "",
        "order": "created_at, tenant_id, owner_user_id, event_id",
    },
    {
        "class": "postgres.pipeline_outbox",
        "table": "pipeline_outbox",
        "time_column": "created_at",
        "key": "ctid",
        "extra": "status IN ('completed', 'delivered', 'failed', 'done', 'dead') AND (lease_expires_at IS NULL OR lease_expires_at < $1)",
        "order": "created_at, id",
    },
    {
        "class": "postgres.pipeline_ledger",
        "table": "pipeline_ledger",
        "time_column": "created_at",
        "key": "ctid",
        "extra": "",
        "order": "created_at, tenant_id, owner_user_id, stage, event_id",
    },
    {
        "class": "postgres.email_outbox",
        "table": "email_outbox",
        "time_column": "created_at",
        "key": "ctid",
        "extra": "status IN ('delivered', 'failed') AND (lease_expires_at IS NULL OR lease_expires_at < $1)",
        "order": "created_at, id",
    },
    {
        "class": "postgres.raw_logs",
        "table": "logs",
        "time_column": "ingested_at",
        "key": "ctid",
        "extra": "",
        "order": "ingested_at, tenant_id, owner_user_id, id",
    },
)

CHILD_FIRST = [
    "postgres.anomaly_events",
    "postgres.tracking_loops",
    "postgres.feature_windows",
    "postgres.incidents",
    "postgres.feature_inputs",
    "postgres.pipeline_outbox",
    "postgres.pipeline_ledger",
    "postgres.email_outbox",
    "postgres.raw_logs",
]


def _dsn() -> str:
    configured = os.getenv("RETENTION_DATABASE_URL") or os.getenv("DATABASE_URL")
    if configured:
        return configured.replace("postgresql+asyncpg://", "postgresql://", 1)
    required = {
        "host": os.getenv("POSTGRES_HOST", ""),
        "user": os.getenv("POSTGRES_USER", ""),
        "database": os.getenv("POSTGRES_DB", ""),
    }
    if not all(required.values()):
        raise RetentionError("database connection is not configured")
    password = os.getenv("POSTGRES_PASSWORD", "")
    return (
        f"postgresql://{required['user']}:{password}@{required['host']}:{os.getenv('POSTGRES_PORT', '5432')}"
        f"/{required['database']}"
    )


def _duration_cutoff(now: dt.datetime, value: str, grace: str) -> dt.datetime | None:
    duration = parse_duration(value)
    grace_period = parse_duration(grace)
    if duration is None:
        return None
    return now - duration - (grace_period or dt.timedelta())


def _class_index(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return policy["storage_classes"]


async def _schema_inventory(connection: asyncpg.Connection) -> dict[str, Any]:
    tables = {
        str(row["table_name"])
        for row in await connection.fetch(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """
        )
    }
    columns: dict[str, set[str]] = {}
    for row in await connection.fetch(
        """
        SELECT table_name, column_name FROM information_schema.columns
        WHERE table_schema = 'public'
        """
    ):
        columns.setdefault(str(row["table_name"]), set()).add(str(row["column_name"]))
    foreign_keys = [
        dict(row)
        for row in await connection.fetch(
            """
            SELECT conrelid::regclass::text AS child_table,
                   confrelid::regclass::text AS parent_table,
                   conname
            FROM pg_constraint
            WHERE contype = 'f' AND connamespace = 'public'::regnamespace
            ORDER BY child_table, conname
            """
        )
    ]
    return {
        "tables": sorted(tables),
        "columns": {name: sorted(values) for name, values in sorted(columns.items())},
        "foreign_keys": foreign_keys,
    }


async def _server_now(connection: asyncpg.Connection) -> dt.datetime:
    value = await connection.fetchval("SELECT clock_timestamp() AT TIME ZONE 'UTC'")
    if not isinstance(value, dt.datetime):
        raise RetentionError("database UTC clock unavailable")
    return value.replace(tzinfo=UTC)


async def _lock(connection: asyncpg.Connection, key: str) -> bool:
    lock_value = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:4], "big", signed=True)
    return bool(await connection.fetchval("SELECT pg_try_advisory_lock($1, $2)", LOCK_NAMESPACE, lock_value))


async def _unlock(connection: asyncpg.Connection, key: str) -> None:
    lock_value = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:4], "big", signed=True)
    await connection.fetchval("SELECT pg_advisory_unlock($1, $2)", LOCK_NAMESPACE, lock_value)


def _metadata_only_plan(
    policy: dict[str, Any], reason: str, *, exclude: set[str] | None = None
) -> dict[str, Any]:
    excluded = exclude or set()
    classes = []
    for class_name, definition in sorted(_class_index(policy).items()):
        if class_name in excluded:
            continue
        classes.append(
            {
                "storage_class": class_name,
                "candidate_count": None,
                "candidate_bytes": None,
                "cutoff": None,
                "blocked_by_guard": reason,
                "action": "PLAN_ONLY",
                "owner": definition["owner"],
                "scope": definition["scope"],
            }
        )
    return {"storage_classes": classes, "schema": None}


async def _plan_database(
    connection: asyncpg.Connection, policy: dict[str, Any]
) -> dict[str, Any]:
    inventory = await _schema_inventory(connection)
    now = await _server_now(connection)
    classes = _class_index(policy)
    planned: list[dict[str, Any]] = []
    for spec in sorted(TABLE_SPECS, key=lambda item: CHILD_FIRST.index(item["class"])):
        definition = classes[spec["class"]]
        cutoff = _duration_cutoff(
            now, definition["retention_duration"], definition["grace_period"]
        )
        base = {
            "storage_class": spec["class"],
            "table": spec["table"],
            "candidate_count": 0,
            "candidate_bytes": 0,
            "cutoff": cutoff.isoformat().replace("+00:00", "Z") if cutoff else None,
            "owner": definition["owner"],
            "scope": definition["scope"],
            "action": "PLAN_ONLY",
            "blocked_by_guard": None,
        }
        if not definition["enabled"]:
            base["blocked_by_guard"] = "storage-class-disabled"
            planned.append(base)
            continue
        if cutoff is None:
            base["blocked_by_guard"] = "non-time-based-retention-requires-specialized-planner"
            planned.append(base)
            continue
        if spec["table"] not in inventory["tables"]:
            base["blocked_by_guard"] = "table-not-present"
            planned.append(base)
            continue
        if spec["time_column"] not in inventory["columns"].get(spec["table"], []):
            base["blocked_by_guard"] = "time-column-not-present"
            planned.append(base)
            continue
        if definition["legal_hold_behavior"] != "none":
            base["blocked_by_guard"] = "legal-hold-contract-not-installed"
            planned.append(base)
            continue
        extra = spec["extra"].replace("$1", "$2") if spec["extra"] else ""
        where = f"{spec['time_column']} < $1"
        args: list[Any] = [cutoff]
        if extra:
            where += f" AND ({extra})"
            if "$2" in extra:
                args.append(now)
        count_row = await connection.fetchrow(
            f"SELECT COUNT(*) AS count, COALESCE(SUM(pg_column_size(t)), 0) AS bytes FROM {spec['table']} AS t WHERE {where}",
            *args,
        )
        base["candidate_count"] = int(count_row["count"])
        base["candidate_bytes"] = int(count_row["bytes"])
        base["action"] = "ELIGIBLE_IF_APPROVED"
        base["blocked_by_guard"] = "policy-approval-and-explicit-apply-required"
        planned.append(base)
    planned.extend(
        _metadata_only_plan(
            policy,
            "specialized-provider-plan-required",
            exclude={spec["class"] for spec in TABLE_SPECS},
        )["storage_classes"]
    )
    return {"storage_classes": planned, "schema": inventory, "server_now": now.isoformat().replace("+00:00", "Z")}


async def _apply_table(
    connection: asyncpg.Connection,
    spec: dict[str, Any],
    definition: dict[str, Any],
    cutoff: dt.datetime,
    deadline: float,
) -> int:
    deleted = 0
    batch_size = int(definition["batch_size"])
    while time.monotonic() < deadline:
        if batch_size <= 0:
            return deleted
        async with connection.transaction():
            extra = spec["extra"].replace("$1", "$2") if spec["extra"] else ""
            where = f"{spec['time_column']} < $1"
            args: list[Any] = [cutoff]
            if extra:
                where += f" AND ({extra})"
                if "$2" in extra:
                    args.append(dt.datetime.now(UTC))
            result = await connection.execute(
                f"WITH doomed AS (SELECT ctid FROM {spec['table']} WHERE {where} ORDER BY {spec['order']} FOR UPDATE SKIP LOCKED LIMIT {batch_size}) "
                f"DELETE FROM {spec['table']} WHERE ctid IN (SELECT ctid FROM doomed)",
                *args,
            )
            batch_deleted = int(result.split()[-1])
            if batch_deleted == 0:
                return deleted
            deleted += batch_deleted
    raise RetentionError("retention max runtime exceeded; committed batches remain retryable")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    policy = load_policy(args.policy)
    status = policy_status(policy)
    if args.apply and not status["destructive_apply_enabled"]:
        raise RetentionError("destructive apply requires an approved active policy")
    if args.apply and os.getenv("ENVIRONMENT", "").lower() == "production" and status["mode"] != "ACTIVE":
        raise RetentionError("production retention is not active")
    if args.no_database:
        plan = _metadata_only_plan(policy, "database-not-requested")
    else:
        connection = await asyncpg.connect(_dsn(), timeout=5)
        try:
            lock_key = str(policy["mode"]["lock_key"])
            if not await _lock(connection, lock_key):
                raise RetentionError("another retention execution already holds the lock")
            try:
                plan = await _plan_database(connection, policy)
                if args.apply:
                    if any(item["action"] == "ELIGIBLE_IF_APPROVED" and item["blocked_by_guard"] != "policy-approval-and-explicit-apply-required" for item in plan["storage_classes"]):
                        raise RetentionError("retention plan contains a blocked safety guard")
                    deadline = time.monotonic() + int(policy["mode"]["max_runtime_seconds"])
                    applied: dict[str, int] = {}
                    by_class = {item["class"]: item for item in TABLE_SPECS}
                    for class_name in CHILD_FIRST:
                        definition = policy["storage_classes"][class_name]
                        if not definition["enabled"] or definition["dry_run"]:
                            continue
                        cutoff = _duration_cutoff(
                            await _server_now(connection),
                            definition["retention_duration"],
                            definition["grace_period"],
                        )
                        if cutoff is None or class_name not in by_class:
                            continue
                        applied[class_name] = await _apply_table(
                            connection, by_class[class_name], definition, cutoff, deadline
                        )
                    plan["applied_rows"] = applied
                    for item in plan["storage_classes"]:
                        if item["storage_class"] in applied:
                            item["action"] = "APPLIED"
                            item["blocked_by_guard"] = None
            finally:
                await _unlock(connection, lock_key)
        finally:
            await connection.close()
    return {
        "status": "PASS",
        "mode": "APPLY" if args.apply else "PLAN",
        "policy": status,
        "production_destructive_purge": bool(args.apply and os.getenv("ENVIRONMENT", "").lower() == "production"),
        "plan": plan,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", default=str(ROOT / "config" / "retention-policy.yml"))
    parser.add_argument("--apply", action="store_true", help="request bounded destructive execution")
    parser.add_argument("--no-database", action="store_true", help="validate policy and emit a metadata-only plan")
    args = parser.parse_args()
    try:
        payload = asyncio.run(run(args))
    except (PolicyError, RetentionError, OSError, asyncpg.PostgresError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
