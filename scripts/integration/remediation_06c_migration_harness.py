"""Disposable historical-schema migration evidence for Remediation 06C.

The harness uses pinned historical ``scripts/init.sql`` snapshots from Git to
represent materially distinct schema families.  It then uses the repository's
current lifecycle function/runner to advance isolated PostgreSQL 16 /
TimescaleDB databases.  The pre-tenant-partitioning fixture is intentionally a
safe-stop control because the published 20260826 cutover is frozen history.

Run from the repository root::

    python scripts/integration/remediation_06c_migration_harness.py

Docker is required.  All database data is held in disposable containers and
the evidence contains no passwords or database contents.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import asyncpg
import httpx
from redis.asyncio import Redis

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import database_lifecycle as lifecycle  # noqa: E402


INIT_PATH = REPO_ROOT / "scripts" / "init.sql"
MIGRATION_MANIFEST_PATH = REPO_ROOT / "scripts" / "migration_manifest.json"
FIXTURE_MANIFEST_PATH = (
    REPO_ROOT / "scripts" / "integration" / "remediation_06c_fixtures.json"
)
EVIDENCE_DIR = REPO_ROOT / "temporary-report" / "remediation-06c" / "evidence"
# Multi-architecture manifest-list digests; disposable hosts select their own platform.
POSTGRES_IMAGE = "timescale/timescaledb@sha256:4e459e217f00cbb09920c34d245501e63427e6767a495de57ce76823ff280f12"  # 2.17.2-pg16
VALKEY_IMAGE = "valkey/valkey@sha256:752ba000a58bc8925d11ee863a39b5225617cd2cb3dbc9ec8ac65268b65ebb6d"  # 8.0.2-alpine
FROZEN_VERSION = "20260826_0001_multitenant_partitioning"
BRIDGE_PREDECESSOR = "20260907_0003_archive_security_and_model_registry"
OWNERSHIP_VERSION = "20260913_0010_per_user_data_ownership"
EVENT_ID_VERSION = "20260911_0005_distributed_correctness"
JWT_SECRET = "ls06c-disposable-jwt-secret-not-production"
ENCRYPTION_KEY = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="
UTC = timezone.utc


class HarnessFailure(RuntimeError):
    """Raised when a required disposable assertion fails."""


@dataclass(slots=True)
class Config:
    run_id: str
    postgres_container: str
    valkey_container: str
    postgres_port: int
    valkey_port: int
    postgres_user: str
    postgres_password: str
    control_db: str

    def db_kwargs(self, database: str) -> dict[str, Any]:
        return {
            "user": self.postgres_user,
            "password": self.postgres_password,
            "host": "127.0.0.1",
            "port": self.postgres_port,
            "database": database,
            "timeout": 5,
            "command_timeout": 300,
        }

    def lifecycle_env(self, database: str) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": os.pathsep.join(
                    [str(REPO_ROOT), str(REPO_ROOT / "backend")]
                ),
                "ENVIRONMENT": "test",
                "TEST_MODE": "true",
                "POSTGRES_USER": self.postgres_user,
                "POSTGRES_PASSWORD": self.postgres_password,
                "POSTGRES_HOST": "127.0.0.1",
                "POSTGRES_PORT": str(self.postgres_port),
                "POSTGRES_DB": database,
                "POSTGRES_SSL_MODE": "disable",
                "POSTGRES_ALLOW_INSECURE_TLS": "true",
                "DATABASE_URL": (
                    f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
                    f"@127.0.0.1:{self.postgres_port}/{database}"
                ),
                "REDIS_URL": f"redis://127.0.0.1:{self.valkey_port}/0",
                "JWT_SECRET_KEY": JWT_SECRET,
                "ENCRYPTION_KEY": ENCRYPTION_KEY,
                "INGEST_API_KEYS": "",
                "METRICS_TOKEN": "ls06c-disposable-metrics",
                "FRONTEND_URL": "http://127.0.0.1",
                "RUN_EMBEDDED_WORKERS": "false",
                "RUN_WEBHOOK_WORKER_IN_LIFESPAN": "false",
                "RUN_ARCHIVE_WORKER_IN_LIFESPAN": "false",
                "DRAIN3_STATE_BACKEND": "file",
                "SSO_UNMAPPED_PROVIDER_POLICY": "deny",
            }
        )
        return environment

    def app_env(
        self, database: str, stream_name: str, state_path: Path
    ) -> dict[str, str]:
        environment = self.lifecycle_env(database)
        environment.update(
            {
                "LOG_STREAM_NAME": stream_name,
                "LOG_WORKERS_GROUP": "log_workers",
                "DRAIN3_STATE_PATH": str(state_path),
                "DRAIN3_BATCH_SIZE": "500",
                "DRAIN3_FLUSH_INTERVAL_SECONDS": "5.0",
                "GRAPH_SCORING_ENABLED": "false",
                "ARCHIVE_HOT_RETENTION_DAYS": "30",
                "USE_MOCK_S3": "true",
            }
        )
        return environment


@dataclass(slots=True)
class ManagedProcess:
    process: subprocess.Popen[str]
    log_file: Any
    log_path: Path

    @classmethod
    def start(
        cls,
        args: list[str],
        *,
        environment: dict[str, str],
        log_path: Path,
    ) -> "ManagedProcess":
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("w", encoding="utf-8")
        process = subprocess.Popen(
            args,
            cwd=REPO_ROOT,
            env=environment,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        return cls(process=process, log_file=log_file, log_path=log_path)

    def stop(self, *, force: bool = False) -> None:
        if self.process.poll() is None:
            if force:
                self.process.kill()
            else:
                self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=15)
        if not self.log_file.closed:
            self.log_file.close()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def make_config() -> Config:
    run_id = secrets.token_hex(6)
    return Config(
        run_id=run_id,
        postgres_container=f"logsentinel-06c-migration-pg-{run_id}",
        valkey_container=f"logsentinel-06c-migration-valkey-{run_id}",
        postgres_port=_free_port(),
        valkey_port=_free_port(),
        postgres_user="logsentinel",
        postgres_password=f"ls06c-db-{secrets.token_urlsafe(18)}",
        control_db=f"ls06c_control_{run_id}",
    )


def _docker(
    *args: str, timeout: float = 180.0, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["docker", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        raise HarnessFailure(
            f"docker {' '.join(args[:4])} failed with exit {result.returncode}"
        )
    return result


def start_services(config: Config) -> None:
    _docker(
        "run",
        "-d",
        "--name",
        config.postgres_container,
        "--tmpfs",
        "/var/lib/postgresql/data",
        "--shm-size",
        "256m",
        "-e",
        f"POSTGRES_USER={config.postgres_user}",
        "-e",
        f"POSTGRES_PASSWORD={config.postgres_password}",
        "-e",
        f"POSTGRES_DB={config.control_db}",
        "-p",
        f"127.0.0.1:{config.postgres_port}:5432",
        POSTGRES_IMAGE,
        "postgres",
        "-c",
        "fsync=on",
        "-c",
        "synchronous_commit=on",
        "-c",
        "full_page_writes=on",
        "-c",
        "max_connections=100",
        timeout=300,
    )
    _docker(
        "run",
        "-d",
        "--name",
        config.valkey_container,
        "--tmpfs",
        "/data",
        "-p",
        f"127.0.0.1:{config.valkey_port}:6379",
        VALKEY_IMAGE,
        "valkey-server",
        "--appendonly",
        "yes",
        "--appendfsync",
        "always",
        "--save",
        "",
        "--maxmemory",
        "256mb",
        "--maxmemory-policy",
        "noeviction",
        timeout=120,
    )


def stop_services(config: Config) -> None:
    for name in (config.valkey_container, config.postgres_container):
        _docker("rm", "--force", name, timeout=60, check=False)


async def wait_for_postgres(config: Config) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            connection = await asyncpg.connect(**config.db_kwargs(config.control_db))
            await connection.close()
            return
        except (OSError, asyncpg.PostgresError):
            await asyncio.sleep(1)
    raise HarnessFailure("timed out waiting for disposable PostgreSQL")


async def wait_for_valkey(config: Config) -> None:
    client = Redis.from_url(f"redis://127.0.0.1:{config.valkey_port}/0")
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if await client.ping():
                    return
            except OSError:
                pass
            await asyncio.sleep(1)
    finally:
        await client.aclose()
    raise HarnessFailure("timed out waiting for disposable Valkey")


def _safe_identifier(value: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", value):
        raise HarnessFailure(f"unexpected fixture identifier: {value}")
    return '"' + value + '"'


async def create_database(config: Config, database: str) -> None:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", database):
        raise HarnessFailure("fixture database name is not safe")
    connection = await asyncpg.connect(**config.db_kwargs("postgres"))
    try:
        await connection.execute(f"CREATE DATABASE {_safe_identifier(database)}")
    finally:
        await connection.close()


async def execute_sql(config: Config, database: str, sql: str) -> None:
    connection = await asyncpg.connect(**config.db_kwargs(database))
    try:
        await connection.execute(sql)
    finally:
        await connection.close()


async def connection_for(config: Config, database: str) -> asyncpg.Connection:
    return await asyncpg.connect(**config.db_kwargs(database))


def historical_bootstrap(commit: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{commit}:scripts/init.sql"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise HarnessFailure(f"pinned historical bootstrap {commit} is unavailable")
    return result.stdout


def manifest_through(version: str) -> dict[str, Any]:
    manifest = copy.deepcopy(lifecycle.load_manifest())
    active = manifest["active_migrations"]
    versions = [item["version"] for item in active]
    if version not in versions:
        raise HarnessFailure(f"unknown migration cutoff {version}")
    manifest["active_migrations"] = [
        item for item in active if item["version"] <= version
    ]
    return manifest


async def apply_through(config: Config, database: str, version: str) -> list[str]:
    manifest = manifest_through(version)
    connection = await connection_for(config, database)
    try:
        return await lifecycle.apply_migrations(connection, manifest)
    finally:
        await connection.close()


def run_lifecycle(
    config: Config,
    database: str,
    *,
    environment_overrides: dict[str, str] | None = None,
    timeout: float = 240,
) -> dict[str, Any]:
    environment = config.lifecycle_env(database)
    if environment_overrides:
        environment.update(environment_overrides)
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "scripts/database_lifecycle.py", "--apply"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    duration = time.monotonic() - started
    return {
        "exit_code": result.returncode,
        "duration_seconds": round(duration, 3),
        "stdout_tail": result.stdout.splitlines()[-20:],
        "stderr_tail": result.stderr.splitlines()[-20:],
        "applied": _parse_json_output(result.stdout),
    }


def _parse_json_output(output: str) -> Any:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return None
    for line in reversed(lines):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


async def ledger(config: Config, database: str) -> list[str]:
    connection = await connection_for(config, database)
    try:
        if (
            await connection.fetchval("SELECT to_regclass('public.schema_migrations')")
            is None
        ):
            return []
        rows = await connection.fetch(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
        return [str(row["version"]) for row in rows]
    finally:
        await connection.close()


async def table_columns(config: Config, database: str, table: str) -> set[str]:
    connection = await connection_for(config, database)
    try:
        rows = await connection.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = $1
            """,
            table,
        )
        return {str(row["column_name"]) for row in rows}
    finally:
        await connection.close()


async def row_counts(config: Config, database: str) -> dict[str, int]:
    tables = [
        "tenants",
        "users",
        "external_identities",
        "tenant_memberships",
        "ingestion_api_keys",
        "logs",
        "feature_windows",
        "anomaly_events",
        "tracking_loops",
        "incidents",
        "pipeline_ledger",
        "pipeline_outbox",
        "pipeline_feature_inputs",
        "archive_manifest",
        "archive_rehydration_sessions",
        "model_artifacts",
        "tenant_integrations",
        "email_outbox",
        "legacy_cleanup_audit",
    ]
    connection = await connection_for(config, database)
    try:
        result: dict[str, int] = {}
        for table in tables:
            if await connection.fetchval("SELECT to_regclass($1)", f"public.{table}"):
                result[table] = int(
                    await connection.fetchval(
                        f"SELECT count(*) FROM {_safe_identifier(table)}"
                    )
                )
        return result
    finally:
        await connection.close()


async def schema_identity(config: Config, database: str) -> dict[str, Any]:
    connection = await connection_for(config, database)
    try:
        versions = await ledger(config, database)
        logs_pk = await connection.fetchval(
            """
            SELECT string_agg(a.attname, ',' ORDER BY k.ordinality)
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum, ordinality)
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
            WHERE i.indisprimary AND n.nspname = 'public' AND c.relname = 'logs'
            """
        )
        hypertable = await connection.fetchval(
            """
            SELECT 1 FROM timescaledb_information.hypertables
            WHERE hypertable_schema = 'public' AND hypertable_name = 'logs'
            """
        )
        extension = await connection.fetchval(
            "SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'"
        )
        logs_columns = await connection.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'logs'
            ORDER BY ordinal_position
            """
        )
        return {
            "migration_ledger": versions,
            "logs_primary_key": logs_pk,
            "logs_hypertable": bool(hypertable),
            "timescaledb_extension": bool(extension),
            "logs_columns": [str(row["column_name"]) for row in logs_columns],
        }
    finally:
        await connection.close()


async def checksums(config: Config, database: str) -> dict[str, Any]:
    manifest = lifecycle.load_manifest()
    connection = await connection_for(config, database)
    try:
        result: dict[str, Any] = {}
        for migration, path in lifecycle.active_migration_files(manifest):
            stored = await connection.fetchval(
                "SELECT checksum FROM schema_migrations WHERE version = $1",
                migration["version"],
            )
            expected = lifecycle._sha256(path)
            result[migration["version"]] = {
                "stored": stored,
                "expected": expected,
                "match_or_bootstrap_null": stored in (None, expected),
            }
        return result
    finally:
        await connection.close()


async def invariant_counts(config: Config, database: str) -> dict[str, int]:
    connection = await connection_for(config, database)
    try:
        tables = [
            "logs",
            "feature_windows",
            "anomaly_events",
            "tracking_loops",
            "incidents",
            "pipeline_ledger",
            "pipeline_outbox",
            "pipeline_feature_inputs",
            "archive_manifest",
            "archive_rehydration_sessions",
            "model_artifacts",
            "tenant_integrations",
        ]
        result = {
            "default_tenant_rows": 0,
            "orphan_memberships": 0,
            "orphan_api_keys": 0,
            "ownerless_operational_rows": 0,
            "tenant_owner_mismatches": 0,
            "orphan_pipeline_rows": 0,
            "orphan_derived_rows": 0,
            "duplicate_logical_ids": 0,
        }
        if await connection.fetchval("SELECT to_regclass('public.tenants')"):
            result["default_tenant_rows"] = int(
                await connection.fetchval(
                    "SELECT count(*) FROM tenants WHERE id = 'default'"
                )
            )
        if await connection.fetchval("SELECT to_regclass('public.tenant_memberships')"):
            result["orphan_memberships"] = int(
                await connection.fetchval(
                    """
                    SELECT count(*) FROM tenant_memberships m
                    LEFT JOIN tenants t ON t.id = m.tenant_id
                    LEFT JOIN users u ON u.id = m.user_id
                    WHERE t.id IS NULL OR u.id IS NULL
                    """
                )
            )
        if await connection.fetchval("SELECT to_regclass('public.ingestion_api_keys')"):
            result["orphan_api_keys"] = int(
                await connection.fetchval(
                    """
                    SELECT count(*) FROM ingestion_api_keys k
                    LEFT JOIN tenants t ON t.id = k.tenant_id
                    LEFT JOIN users u ON u.id = k.user_id
                    WHERE t.id IS NULL OR u.id IS NULL
                    """
                )
            )
        for table in tables:
            columns = await table_columns(config, database, table)
            if "owner_user_id" not in columns:
                continue
            result["ownerless_operational_rows"] += int(
                await connection.fetchval(
                    f"SELECT count(*) FROM {_safe_identifier(table)} WHERE owner_user_id IS NULL"
                )
            )
            if "tenant_id" in columns:
                result["tenant_owner_mismatches"] += int(
                    await connection.fetchval(
                        f"""
                        SELECT count(*) FROM {_safe_identifier(table)} r
                        JOIN users u ON u.id = r.owner_user_id
                        WHERE r.owner_user_id IS NOT NULL
                          AND r.tenant_id <> u.tenant_id
                        """
                    )
                )
        for table in ("pipeline_ledger", "pipeline_outbox", "pipeline_feature_inputs"):
            columns = await table_columns(config, database, table)
            if "owner_user_id" in columns and "tenant_id" in columns:
                result["orphan_pipeline_rows"] += int(
                    await connection.fetchval(
                        f"""
                        SELECT count(*) FROM {_safe_identifier(table)} r
                        LEFT JOIN users u ON u.id = r.owner_user_id
                        WHERE r.owner_user_id IS NOT NULL
                          AND (u.id IS NULL OR u.tenant_id <> r.tenant_id)
                        """
                    )
                )
        derived_tables_exist = True
        for table in ("feature_windows", "anomaly_events", "tracking_loops"):
            if not await connection.fetchval(f"SELECT to_regclass('public.{table}')"):
                derived_tables_exist = False
                break
        if derived_tables_exist:
            feature_columns = await table_columns(config, database, "feature_windows")
            derived_columns = await table_columns(config, database, "anomaly_events")
            if {"tenant_id", "owner_user_id"} <= feature_columns and {
                "tenant_id",
                "owner_user_id",
            } <= derived_columns:
                result["orphan_derived_rows"] += int(
                    await connection.fetchval(
                        """
                        SELECT count(*) FROM anomaly_events a
                        LEFT JOIN feature_windows f
                          ON f.tenant_id = a.tenant_id
                         AND f.owner_user_id = a.owner_user_id
                         AND f.window_id = a.window_id
                        WHERE f.id IS NULL
                        """
                    )
                )
        logs_columns = await table_columns(config, database, "logs")
        if {"tenant_id", "owner_user_id", "event_id"} <= logs_columns:
            result["duplicate_logical_ids"] = int(
                await connection.fetchval(
                    """
                    SELECT count(*) FROM (
                      SELECT tenant_id, owner_user_id, event_id
                      FROM logs GROUP BY tenant_id, owner_user_id, event_id
                      HAVING count(*) > 1
                    ) duplicates
                    """
                )
            )
        return result
    finally:
        await connection.close()


def invariants_passed(counts: dict[str, int]) -> bool:
    return all(value == 0 for value in counts.values())


async def seed_bridge_identities(config: Config, database: str) -> None:
    connection = await connection_for(config, database)
    try:
        async with connection.transaction():
            user_ids = [5, 6, 7, 8, 12, 14]
            for user_id in user_ids:
                await connection.execute(
                    """
                    INSERT INTO users (
                        id, email, hashed_password, full_name, organization,
                        tenant_id, status, role, email_verified_at,
                        password_changed_at
                    ) VALUES ($1, $2, $3, $4, $5, 'default', 'active', 'viewer', $6, NULL)
                    """,
                    user_id,
                    f"legacy-{user_id}@example.test",
                    None if user_id == 14 else "legacy-hash",
                    f"Legacy User {user_id}",
                    "Legacy Organization",
                    datetime(2026, 8, 31, tzinfo=UTC),
                )
            for index, user_id in enumerate(user_ids, start=1):
                await connection.execute(
                    """
                    INSERT INTO external_identities (
                        user_id, provider, issuer, subject, tenant_id,
                        provider_object_id, email, display_name
                    ) VALUES ($1, 'google', 'https://accounts.google.com', $2,
                              NULL, $3, $4, $5)
                    """,
                    user_id,
                    f"legacy-subject-{index}",
                    f"legacy-provider-{index}",
                    f"legacy-{user_id}@example.test",
                    f"Legacy User {user_id}",
                )
            await connection.execute(
                "SELECT setval(pg_get_serial_sequence('users', 'id'), 14, true)"
            )
    finally:
        await connection.close()


async def seed_legacy_operational(
    config: Config,
    database: str,
    *,
    full: bool = False,
    large_logs: int = 3153,
) -> dict[str, int]:
    connection = await connection_for(config, database)
    base_time = datetime(2026, 8, 31, tzinfo=UTC)
    try:
        async with connection.transaction():
            logs_have_event_id = "event_id" in await table_columns(
                config, database, "logs"
            )
            logs = []
            for index in range(large_logs):
                event_time = base_time + timedelta(seconds=index % 86400)
                row = (
                    f"legacy-{index:018d}",
                    "default",
                    event_time,
                    "legacy-service" if index % 2 else "boundary-service",
                    f"legacy message {index}",
                    f"legacy-template-{index % 7}",
                    None if index % 13 == 0 else f"legacy template {index % 7}",
                    "[]",
                    None
                    if index % 11 == 0
                    else ("ERROR" if index % 5 == 0 else "INFO"),
                    "telemetry-generator",
                    "stress-test",
                    None if index % 17 == 0 else f"corr-{index % 31}",
                    json.dumps({"legacy": True, "boundary": index % 13 == 0}),
                    None,
                    event_time,
                    event_time,
                )
                logs.append((row[0], row[0], *row[1:]) if logs_have_event_id else row)
            if logs_have_event_id:
                await connection.executemany(
                    """
                    INSERT INTO logs (
                        id, event_id, tenant_id, timestamp, service, raw_message,
                        template_id, template_text, parameters, level, source,
                        environment, correlation_id, metadata, parsed_at, created_at,
                        ingested_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10,
                              $11, $12, $13, $14::jsonb, $15, $16, $17)
                    """,
                    logs,
                )
            else:
                await connection.executemany(
                    """
                    INSERT INTO logs (
                        id, tenant_id, timestamp, service, raw_message, template_id,
                        template_text, parameters, level, source, environment,
                        correlation_id, metadata, parsed_at, created_at, ingested_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11,
                              $12, $13::jsonb, $14, $15, $16)
                    """,
                    logs,
                )

            windows = []
            for index in range(48):
                start = base_time + timedelta(minutes=index)
                windows.append(
                    (
                        "default",
                        f"legacy-window-{index:03d}",
                        start,
                        start + timedelta(seconds=10),
                        "legacy-service",
                        5,
                        json.dumps({"count": 5, "nullable": index % 9 == 0}),
                    )
                )
            await connection.executemany(
                """
                INSERT INTO feature_windows (
                    tenant_id, window_id, start_time, end_time, service,
                    log_count, feature_vector
                ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                """,
                windows,
            )
            await connection.executemany(
                """
                INSERT INTO anomaly_events (
                    tenant_id, window_id, event_type, severity, score, details
                ) VALUES ($1, $2, 'legacy-anomaly', 'MEDIUM', $3, $4::jsonb)
                """,
                [
                    (
                        "default",
                        f"legacy-window-{index:03d}",
                        0.5 if index % 2 else None,
                        json.dumps({"legacy": True}),
                    )
                    for index in range(48)
                ],
            )
            await connection.executemany(
                """
                INSERT INTO tracking_loops (
                    tenant_id, window_id, anomaly_score, status, blast_radius
                ) VALUES ($1, $2, $3, 'ACTIVE', $4::jsonb)
                """,
                [
                    (
                        "default",
                        f"legacy-window-{index:03d}",
                        0.5 if index % 2 else 0.0,
                        json.dumps({"legacy": True}) if index % 4 else None,
                    )
                    for index in range(48)
                ],
            )

            if full:
                await connection.execute(
                    """
                    INSERT INTO incidents (
                        tenant_id, root_cause, severity, blast_radius, status
                    ) VALUES ('logsentinel', 'legacy full-data incident', 'HIGH', 0.75, 'OPEN')
                    """
                )
                await connection.execute(
                    """
                    INSERT INTO pipeline_ledger (tenant_id, stage, event_id)
                    VALUES ('logsentinel', 'raw', 'legacy-pipeline-event')
                    """
                )
                await connection.execute(
                    """
                    INSERT INTO pipeline_outbox (
                        id, tenant_id, topic, dedup_key, payload, status
                    ) VALUES ('legacy-pipeline-outbox', 'logsentinel', 'feature',
                              'legacy-dedup', '{"legacy": true}'::jsonb, 'pending')
                    """
                )
                await connection.execute(
                    """
                    INSERT INTO pipeline_feature_inputs (
                        tenant_id, event_id, event_timestamp, payload
                    ) VALUES ('logsentinel', 'legacy-feature-input', $1, '{"legacy": true}'::jsonb)
                    """,
                    base_time,
                )
                await connection.execute(
                    """
                    INSERT INTO archive_manifest (
                        archive_id, tenant_id, dataset, range_start, range_end,
                        source_chunk_ids, schema_version, idempotency_key,
                        object_key, sidecar_key, status
                    ) VALUES (
                        '00000000-0000-0000-0000-000000000006', 'logsentinel',
                        'raw_logs', $1, $2, ARRAY['legacy-chunk'], 1,
                        'legacy-archive-key', 'legacy/object.parquet',
                        'legacy/object.json', 'HOT'
                    )
                    """,
                    base_time,
                    base_time + timedelta(minutes=1),
                )
                await connection.execute(
                    """
                    INSERT INTO archive_rehydration_sessions (
                        staging_table, tenant_id, archive_ids, expires_at, status
                    ) VALUES ('legacy_rehydrate', 'logsentinel',
                              ARRAY['00000000-0000-0000-0000-000000000006']::uuid[],
                              $1, 'pending')
                    """,
                    base_time + timedelta(hours=1),
                )
                await connection.execute(
                    """
                    INSERT INTO model_artifacts (
                        tenant_id, model_id, version, training_range,
                        feature_schema_version, artifact_uri, checksum, status
                    ) VALUES ('logsentinel', 'legacy-model', 1,
                              '[2026-08-31 00:00:00+00,2026-09-01 00:00:00+00)',
                              'legacy-v1', 's3://legacy/model',
                              'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                              'candidate')
                    """
                )
                await connection.execute(
                    """
                    INSERT INTO tenant_integrations (
                        tenant_id, provider, destination_url, enabled
                    ) VALUES ('logsentinel', 'slack', 'https://example.test/hooks/legacy', true)
                    """
                )
                await connection.execute(
                    """
                    INSERT INTO email_outbox (
                        id, user_id, tenant_id, kind, recipient, template_data,
                        idempotency_key
                    ) VALUES ('legacy-email', 5, 'logsentinel', 'verification',
                              'legacy@example.test', '{}', 'legacy-email-key')
                    """
                )
                await connection.execute(
                    """
                    INSERT INTO tenant_settings (tenant_id, settings, updated_by)
                    VALUES ('logsentinel', '{"legacy": true}'::jsonb, 5)
                    """
                )
        return {
            "logs": large_logs,
            "feature_windows": 48,
            "anomaly_events": 48,
            "tracking_loops": 48,
        }
    finally:
        await connection.close()


async def mark_frozen_history(config: Config, database: str) -> None:
    digest = lifecycle._sha256(
        REPO_ROOT / "scripts" / "migrations" / f"{FROZEN_VERSION}.sql"
    )
    connection = await connection_for(config, database)
    try:
        await connection.execute(
            """
            INSERT INTO schema_migrations (version, checksum, description)
            VALUES ($1, $2, $3) ON CONFLICT (version) DO NOTHING
            """,
            FROZEN_VERSION,
            digest,
            "Fixture records the previously validated frozen historical cutover",
        )
    finally:
        await connection.close()


async def prepare_fixture(
    config: Config,
    database: str,
    definition: dict[str, Any],
) -> dict[str, Any]:
    await create_database(config, database)
    await execute_sql(config, database, historical_bootstrap("88d34e3"))
    await mark_frozen_history(config, database)
    profile = definition["data_profile"]
    if profile in {"legacy_clean", "legacy_full"}:
        await apply_through(config, database, BRIDGE_PREDECESSOR)
        if definition["id"] == "legacy-bridge-to-current" or profile == "legacy_full":
            await seed_bridge_identities(config, database)
    await apply_through(config, database, definition["apply_through"])
    if profile == "legacy_clean":
        seeded = await seed_legacy_operational(config, database)
    elif profile == "legacy_full":
        seeded = await seed_legacy_operational(config, database, full=True)
    else:
        seeded = {}
    return {
        "source_schema": await schema_identity(config, database),
        "initial_row_counts": await row_counts(config, database),
        "seeded_legacy_counts": seeded,
    }


async def prepare_pre_tenant_fixture(config: Config, database: str) -> dict[str, Any]:
    await create_database(config, database)
    await execute_sql(config, database, historical_bootstrap("dca8188"))
    return {
        "source_schema": await schema_identity(config, database),
        "initial_row_counts": await row_counts(config, database),
    }


async def run_application_compatibility(
    config: Config,
    database: str,
    work_dir: Path,
    *,
    label: str,
) -> dict[str, Any]:
    api_port = _free_port()
    stream_name = f"logs:stream:06c:{config.run_id}:{label}"
    environment = config.app_env(
        database,
        stream_name,
        work_dir / f"{label}-drain-state.bin",
    )
    api = ManagedProcess.start(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(api_port),
            "--log-level",
            "warning",
        ],
        environment=environment,
        log_path=work_dir / f"{label}-api.log",
    )
    worker_environment = dict(environment)
    worker_environment["WORKER_INSTANCE_ID"] = f"06c-{label}"
    worker = ManagedProcess.start(
        [sys.executable, "-m", "backend.app.cli.worker", "run", "pipeline"],
        environment=worker_environment,
        log_path=work_dir / f"{label}-pipeline.log",
    )
    redis = Redis.from_url(
        f"redis://127.0.0.1:{config.valkey_port}/0", decode_responses=True
    )
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{api_port}", timeout=3
        ) as client:
            deadline = time.monotonic() + 60
            api_status = None
            while time.monotonic() < deadline:
                try:
                    response = await client.get("/readiness")
                    api_status = response.status_code
                    if api_status == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)
            if api_status != 200:
                raise HarnessFailure(f"current API readiness failed for {label}")
        heartbeat_key = f"logsentinel:worker-heartbeat:pipeline:06c-{label}"
        heartbeat_deadline = time.monotonic() + 60
        heartbeat = False
        while time.monotonic() < heartbeat_deadline:
            if await redis.get(heartbeat_key):
                heartbeat = True
                break
            await asyncio.sleep(0.25)
        if not heartbeat:
            raise HarnessFailure(
                f"current pipeline worker heartbeat failed for {label}"
            )
        return {
            "startup": "PASS",
            "db_readiness": "PASS",
            "schema_readiness": "PASS",
            "worker_schema_gate": "PASS",
            "worker_heartbeat": "PASS",
        }
    finally:
        await redis.aclose()
        worker.stop(force=True)
        api.stop(force=True)


async def run_interruption(
    config: Config,
    work_dir: Path,
    *,
    version: str,
    phase: str,
    cutoff: str,
    seed_event_data: bool,
) -> dict[str, Any]:
    database = f"ls06c_interrupt_{version[-4:]}_{phase[:10]}_{config.run_id}"
    await create_database(config, database)
    await execute_sql(config, database, historical_bootstrap("88d34e3"))
    await mark_frozen_history(config, database)
    await apply_through(config, database, cutoff)
    if seed_event_data:
        await seed_legacy_operational(config, database)
    marker = work_dir / f"interrupt-{version}-{phase}.marker"
    overrides = {
        "LOGSENTINEL_ALLOW_TEST_HOOKS": "1",
        "LOGSENTINEL_TEST_INTERRUPT_VERSION": version,
        "LOGSENTINEL_TEST_INTERRUPT_PHASE": phase,
        "LOGSENTINEL_TEST_INTERRUPT_MARKER": str(marker),
        "LOGSENTINEL_TEST_INTERRUPT_RELEASE": str(work_dir / "never-release"),
        "LOGSENTINEL_TEST_INTERRUPT_TIMEOUT": "120",
    }
    environment = config.lifecycle_env(database)
    environment.update(overrides)
    log_path = work_dir / f"interrupt-{version}-{phase}.log"
    process = ManagedProcess.start(
        [sys.executable, "scripts/database_lifecycle.py", "--apply"],
        environment=environment,
        log_path=log_path,
    )
    try:
        deadline = time.monotonic() + 45
        while not marker.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        if not marker.exists():
            raise HarnessFailure(
                f"migration interruption marker not observed for {version}"
            )
        process.stop(force=True)
        await asyncio.sleep(0.75)
        interrupted_ledger = await ledger(config, database)
        interrupted_counts = await row_counts(config, database)
        interrupted_columns = await table_columns(config, database, "logs")
        resumed = await asyncio.to_thread(run_lifecycle, config, database)
        if resumed["exit_code"] != 0:
            raise HarnessFailure(
                f"migration did not resume after {version} interruption"
            )
        resumed_schema = await schema_identity(config, database)
        resumed_invariants = await invariant_counts(config, database)
        expected_rollback = version == OWNERSHIP_VERSION or version == EVENT_ID_VERSION
        rollback_observed = version not in interrupted_ledger and (
            not expected_rollback
            or (
                "event_id" not in interrupted_columns
                if version == EVENT_ID_VERSION
                else "owner_user_id" not in interrupted_columns
            )
        )
        if not rollback_observed:
            raise HarnessFailure(
                f"interrupted migration left unexpected state for {version}"
            )
        if not invariants_passed(resumed_invariants):
            raise HarnessFailure(f"resumed migration violated invariants for {version}")
        return {
            "migration": version,
            "phase": phase,
            "interruption_observed": True,
            "state_after_termination": {
                "ledger": interrupted_ledger,
                "row_counts": interrupted_counts,
                "logs_columns_include_event_id": "event_id" in interrupted_columns,
                "logs_columns_include_owner_user_id": "owner_user_id"
                in interrupted_columns,
            },
            "rollback_observed": rollback_observed,
            "resume": resumed,
            "resumed_schema": resumed_schema,
            "resumed_invariants": resumed_invariants,
        }
    finally:
        if process.process.poll() is None:
            process.stop(force=True)


async def run_locking_rehearsal(config: Config) -> dict[str, Any]:
    database = f"ls06c_lock_{config.run_id}"
    definition = {
        "id": "pre-per-user-ownership",
        "apply_through": "20260912_0009_pipeline_feature_durability",
        "data_profile": "legacy_clean",
    }
    prepared = await prepare_fixture(config, database, definition)
    blocker = await connection_for(config, database)
    try:
        await blocker.execute("BEGIN")
        await blocker.execute("LOCK TABLE logs IN SHARE MODE")
        blocked = await asyncio.to_thread(run_lifecycle, config, database, timeout=30)
    finally:
        await blocker.execute("ROLLBACK")
        await blocker.close()
    state_after_block = {
        "ledger": await ledger(config, database),
        "row_counts": await row_counts(config, database),
        "logs_columns": await table_columns(config, database, "logs"),
    }
    if blocked["exit_code"] == 0 or OWNERSHIP_VERSION in state_after_block["ledger"]:
        raise HarnessFailure(
            "lock timeout did not fail closed before ownership migration"
        )
    resumed = await asyncio.to_thread(run_lifecycle, config, database)
    if resumed["exit_code"] != 0:
        raise HarnessFailure("ownership migration did not complete after lock release")
    return {
        "migration": OWNERSHIP_VERSION,
        "migration_lock": "ACCESS EXCLUSIVE",
        "blocker_lock": "SHARE",
        "blocked_operation": "LOCK TABLE logs, feature_windows, anomaly_events, tracking_loops",
        "lock_timeout_seconds": 5,
        "observed_blocked_result": blocked,
        "blocked_state_unchanged": state_after_block,
        "resume_result": resumed,
        "resume_invariants": await invariant_counts(config, database),
        "source_fixture": prepared,
        "production_scale_estimate": "NOT CLAIMED",
    }


async def run_large_backfill(config: Config) -> dict[str, Any]:
    database = f"ls06c_large_{config.run_id}"
    await create_database(config, database)
    await execute_sql(config, database, historical_bootstrap("88d34e3"))
    await mark_frozen_history(config, database)
    await apply_through(
        config, database, "20260911_0004_legacy_logsentinel_tenant_bridge"
    )
    seeded = await seed_legacy_operational(config, database, large_logs=20000)
    started = time.monotonic()
    applied = await apply_through(config, database, EVENT_ID_VERSION)
    duration = time.monotonic() - started
    connection = await connection_for(config, database)
    try:
        event_count = int(
            await connection.fetchval(
                "SELECT count(*) FROM logs WHERE event_id IS NOT NULL"
            )
        )
    finally:
        await connection.close()
    full_attempt = await asyncio.to_thread(run_lifecycle, config, database)
    state_after_full_attempt = await row_counts(config, database)
    if event_count != 20000 or EVENT_ID_VERSION not in await ledger(config, database):
        raise HarnessFailure("large event-id backfill did not reconcile all rows")
    if full_attempt["exit_code"] == 0:
        raise HarnessFailure(
            "0010 unexpectedly accepted unsupported large legacy baseline"
        )
    return {
        "dataset": "bounded-large-historical-logs",
        "row_count": 20000,
        "seeded_counts": seeded,
        "migration": EVENT_ID_VERSION,
        "applied": applied,
        "event_id_rows_after_backfill": event_count,
        "backfill_duration_seconds": round(duration, 3),
        "backfill_throughput_rows_per_second": round(20000 / duration, 2)
        if duration > 0
        else None,
        "full_lifecycle_result": full_attempt,
        "state_after_full_lifecycle_attempt": state_after_full_attempt,
        "capacity_claim": "NOT A PRODUCTION CAPACITY BENCHMARK",
    }


async def run_ledger_tests(config: Config) -> dict[str, Any]:
    current = f"ls06c_ledger_current_{config.run_id}"
    await create_database(config, current)
    await execute_sql(config, current, INIT_PATH.read_text(encoding="utf-8"))
    first = await asyncio.to_thread(run_lifecycle, config, current)
    second = await asyncio.to_thread(run_lifecycle, config, current)
    if first["exit_code"] != 0 or second["exit_code"] != 0:
        raise HarnessFailure("current ledger lifecycle did not converge")

    checksum_db = f"ls06c_ledger_checksum_{config.run_id}"
    await create_database(config, checksum_db)
    await execute_sql(config, checksum_db, INIT_PATH.read_text(encoding="utf-8"))
    await asyncio.to_thread(run_lifecycle, config, checksum_db)
    connection = await connection_for(config, checksum_db)
    try:
        await connection.execute(
            """
            UPDATE schema_migrations SET checksum = repeat('0', 64)
            WHERE version = '20260911_0005_distributed_correctness'
            """
        )
    finally:
        await connection.close()
    checksum_result = await asyncio.to_thread(run_lifecycle, config, checksum_db)

    gap_db = f"ls06c_ledger_gap_{config.run_id}"
    await create_database(config, gap_db)
    await execute_sql(config, gap_db, INIT_PATH.read_text(encoding="utf-8"))
    await asyncio.to_thread(run_lifecycle, config, gap_db)
    connection = await connection_for(config, gap_db)
    try:
        await connection.execute(
            "DELETE FROM schema_migrations WHERE version = '20260911_0006_durable_webhook_delivery'"
        )
    finally:
        await connection.close()
    gap_result = await asyncio.to_thread(run_lifecycle, config, gap_db)

    partial_db = f"ls06c_ledger_partial_{config.run_id}"
    definition = {
        "id": "pre-tenant-authority",
        "apply_through": "20260912_0007_application_release_gates",
        "data_profile": "legacy_clean",
    }
    await prepare_fixture(config, partial_db, definition)
    partial_result = await asyncio.to_thread(run_lifecycle, config, partial_db)
    if partial_result["exit_code"] != 0:
        raise HarnessFailure("partially migrated fixture did not converge")
    return {
        "fully_current_duplicate_invocation": {
            "first": first,
            "second": second,
            "second_applied_empty": second.get("applied") == [],
        },
        "checksum_mismatch": {
            "result": checksum_result,
            "fail_closed": checksum_result["exit_code"] != 0,
        },
        "missing_middle_ledger_entry": {
            "result": gap_result,
            "fail_closed": gap_result["exit_code"] != 0,
        },
        "partially_migrated_database": {
            "result": partial_result,
            "converged": partial_result["exit_code"] == 0,
        },
    }


async def run_scenario(config: Config, work_dir: Path) -> dict[str, Any]:
    fixture_manifest = json.loads(FIXTURE_MANIFEST_PATH.read_text(encoding="utf-8"))
    evidence: dict[str, Any] = {
        "status": "PASS",
        "run_id": config.run_id,
        "images": {"postgres": POSTGRES_IMAGE, "valkey": VALKEY_IMAGE},
        "fixture_manifest": fixture_manifest,
    }
    fresh_control = config.control_db
    await execute_sql(config, fresh_control, INIT_PATH.read_text(encoding="utf-8"))
    fresh_result = await asyncio.to_thread(run_lifecycle, config, fresh_control)
    if fresh_result["exit_code"] != 0:
        raise HarnessFailure("fresh current-schema control did not converge")
    fresh_schema = await schema_identity(config, fresh_control)
    fresh_invariants = await invariant_counts(config, fresh_control)
    if not lifecycle.validate_schema:
        raise HarnessFailure("lifecycle verifier unavailable")
    evidence["fresh_install_control"] = {
        "result": fresh_result,
        "schema": fresh_schema,
        "invariants": fresh_invariants,
        "pass": fresh_schema["logs_hypertable"]
        and fresh_schema["timescaledb_extension"]
        and invariants_passed(fresh_invariants),
    }

    matrix: list[dict[str, Any]] = []
    for definition in fixture_manifest["fixtures"]:
        database = f"ls06c_{definition['id'].replace('-', '_')}_{config.run_id}"
        prepared = await prepare_fixture(config, database, definition)
        migration_result = await asyncio.to_thread(run_lifecycle, config, database)
        post_counts = await row_counts(config, database)
        post_schema = await schema_identity(config, database)
        matrix_row: dict[str, Any] = {
            "historical_state": definition["id"],
            "source_git_commit": definition["source_git_commit"],
            "migration_start": definition["source_migration_start"],
            "migration_end": OWNERSHIP_VERSION,
            "source": prepared,
            "migration_result": migration_result,
            "post_migration_row_counts": post_counts,
            "post_migration_schema": post_schema,
        }
        if definition["data_profile"] == "legacy_full":
            matrix_row["result"] = "SAFE_STOP_EXPECTED"
            matrix_row["data_reconciliation"] = {
                "source_rows_unchanged_after_refusal": post_counts
                == prepared["initial_row_counts"],
                "safe_stop": migration_result["exit_code"] != 0,
            }
            matrix_row["invariants_after_refusal"] = await invariant_counts(
                config, database
            )
            if (
                migration_result["exit_code"] == 0
                or post_counts != prepared["initial_row_counts"]
            ):
                raise HarnessFailure("full-data negative control did not fail closed")
        else:
            checks = await checksums(config, database)
            invariants = await invariant_counts(config, database)
            matrix_row["result"] = (
                "PASS" if migration_result["exit_code"] == 0 else "FAIL"
            )
            matrix_row["checksums"] = checks
            matrix_row["invariants"] = invariants
            matrix_row["data_reconciliation"] = {
                "legacy_cleanup_expected": definition["data_profile"] == "legacy_clean",
                "post_operational_rows": post_counts,
                "cleanup_audit_rows": post_counts.get("legacy_cleanup_audit", 0),
            }
            if migration_result["exit_code"] != 0 or not invariants_passed(invariants):
                raise HarnessFailure(
                    f"migration matrix path failed: {definition['id']}"
                )
            matrix_row[
                "application_compatibility"
            ] = await run_application_compatibility(
                config,
                database,
                work_dir,
                label=definition["id"],
            )
        matrix.append(matrix_row)
    evidence["migration_matrix"] = matrix

    pre_tenant_db = f"ls06c_pre_tenant_partitioning_{config.run_id}"
    pre_tenant = await prepare_pre_tenant_fixture(config, pre_tenant_db)
    pre_tenant_result = await asyncio.to_thread(run_lifecycle, config, pre_tenant_db)
    evidence["pre_tenant_partitioning_safe_stop"] = {
        "source": pre_tenant,
        "result": pre_tenant_result,
        "ledger_after_refusal": await ledger(config, pre_tenant_db),
        "safe_stop": pre_tenant_result["exit_code"] != 0,
        "reason": "published 20260826_0001 is frozen history; staged cutover is required",
    }
    if pre_tenant_result["exit_code"] == 0:
        raise HarnessFailure(
            "pre-tenant-partitioning state bypassed frozen-history guard"
        )

    evidence["interruption_results"] = [
        await run_interruption(
            config,
            work_dir,
            version=OWNERSHIP_VERSION,
            phase="before_transaction",
            cutoff="20260912_0009_pipeline_feature_durability",
            seed_event_data=True,
        ),
        await run_interruption(
            config,
            work_dir,
            version=OWNERSHIP_VERSION,
            phase="after_sql_before_ledger",
            cutoff="20260912_0009_pipeline_feature_durability",
            seed_event_data=True,
        ),
        await run_interruption(
            config,
            work_dir,
            version=EVENT_ID_VERSION,
            phase="after_sql_before_ledger",
            cutoff="20260911_0004_legacy_logsentinel_tenant_bridge",
            seed_event_data=True,
        ),
    ]
    evidence["resume_results"] = {
        "all_interruption_resumes_passed": all(
            item["rollback_observed"] and item["resume"]["exit_code"] == 0
            for item in evidence["interruption_results"]
        )
    }
    evidence["locking_rehearsal"] = await run_locking_rehearsal(config)
    evidence["large_backfill_rehearsal"] = await run_large_backfill(config)
    evidence["ledger_safety"] = await run_ledger_tests(config)
    evidence[
        "current_application_against_fresh_control"
    ] = await run_application_compatibility(
        config,
        fresh_control,
        work_dir,
        label="fresh-control",
    )
    evidence["migration_disposition"] = "PARTIAL"
    evidence["partial_reason"] = (
        "Representative later-family upgrades and interruption/locking behavior pass, "
        "but the frozen pre-tenant cutover is a safe stop and arbitrary data-bearing "
        "pre-0010 rows cannot converge without an approved forward migration policy."
    )
    return evidence


def source_identity() -> dict[str, Any]:
    def digest(path: Path) -> str:
        value = hashlib.sha256()
        value.update(path.read_bytes())
        return value.hexdigest()

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--short"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    paths = [
        Path("scripts/database_lifecycle.py"),
        Path("scripts/migration_manifest.json"),
        Path("scripts/init.sql"),
        Path("scripts/backup_database.sh"),
        Path("scripts/restore_database.sh"),
        Path("docker-compose.prod.yml"),
        Path("backend/Dockerfile"),
    ]
    path_hashes = {
        str(path): digest(REPO_ROOT / path)
        for path in paths
        if (REPO_ROOT / path).exists()
    }
    manifest_material = "\n".join(
        f"{name}:{value}" for name, value in sorted(path_hashes.items())
    ).encode()
    return {
        "git_head": head,
        "worktree_status_entries": len(status),
        "filesystem_source_manifest_sha256": hashlib.sha256(
            manifest_material
        ).hexdigest(),
        "critical_file_sha256": path_hashes,
    }


def write_evidence(evidence: dict[str, Any]) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / f"migration-{evidence['run_id']}.json"
    evidence["source_identity"] = source_identity()
    path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-services",
        action="store_true",
        help="leave the uniquely named disposable containers running for inspection",
    )
    args = parser.parse_args()
    config = make_config()
    evidence: dict[str, Any] = {"status": "FAIL", "run_id": config.run_id}
    with tempfile.TemporaryDirectory(
        prefix=f"logsentinel-06c-migration-{config.run_id}-"
    ) as raw:
        work_dir = Path(raw)
        try:
            start_services(config)
            asyncio.run(wait_for_postgres(config))
            asyncio.run(wait_for_valkey(config))
            evidence = asyncio.run(run_scenario(config, work_dir))
            path = write_evidence(evidence)
            print(json.dumps({"status": "PASS", "evidence": str(path)}, sort_keys=True))
            return 0
        except Exception as exc:
            traceback.print_exc()
            evidence["status"] = "FAIL"
            evidence["failure_type"] = type(exc).__name__
            evidence["failure"] = str(exc)
            try:
                path = write_evidence(evidence)
                print(
                    json.dumps(
                        {"status": "FAIL", "evidence": str(path)}, sort_keys=True
                    )
                )
            except Exception:
                print(json.dumps({"status": "FAIL", "evidence": None}, sort_keys=True))
            return 1
        finally:
            if not args.keep_services:
                stop_services(config)


if __name__ == "__main__":
    raise SystemExit(main())
