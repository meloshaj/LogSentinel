"""Disposable PG16/TimescaleDB/Valkey evidence harness for Remediation 06B.

The harness launches only an isolated Compose project, applies the repository
owned bootstrap and migration lifecycle, starts the current ASGI application
and current standalone pipeline worker in separate processes, and records
data-bearing evidence without writing any credential values to the result.

Run from the repository root::

    python scripts/integration/remediation_06b_harness.py

Docker Desktop (or another Docker Engine) is required.  The two disposable
service ports bind to loopback only and are selected afresh for every run.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

import asyncpg
import httpx
import jwt
from redis.asyncio import Redis


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = (
    REPO_ROOT / "tests" / "integration" / "docker-compose.remediation-06b.yml"
)
EVIDENCE_DIR = REPO_ROOT / "temporary-report" / "remediation-06b" / "evidence"
GROUP_NAME = "log_workers"
JWT_ISSUER = "logsentinel"
JWT_AUDIENCE = "logsentinel-api"
JWT_ALGORITHM = "HS256"
ENCRYPTION_KEY = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="


class HarnessFailure(RuntimeError):
    """Raised when a required data-bearing assertion is not met."""


@dataclass(slots=True)
class HarnessConfig:
    run_id: str
    compose_project: str
    database: str
    database_user: str
    database_password: str
    postgres_port: int
    valkey_port: int
    api_port: int
    stream_name: str
    fallback_key: str
    jwt_secret: str

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.database_user}:{self.database_password}"
            f"@127.0.0.1:{self.postgres_port}/{self.database}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://127.0.0.1:{self.valkey_port}/0"

    def compose_environment(self) -> dict[str, str]:
        return {
            "LS06B_POSTGRES_USER": self.database_user,
            "LS06B_POSTGRES_PASSWORD": self.database_password,
            "LS06B_POSTGRES_DB": self.database,
            "LS06B_POSTGRES_PORT": str(self.postgres_port),
            "LS06B_VALKEY_PORT": str(self.valkey_port),
        }

    def application_environment(self, *, state_path: Path) -> dict[str, str]:
        environment = os.environ.copy()
        existing_pythonpath = environment.get("PYTHONPATH", "")
        pythonpath = [str(REPO_ROOT), str(REPO_ROOT / "backend")]
        if existing_pythonpath:
            pythonpath.append(existing_pythonpath)
        environment.update(
            {
                "PYTHONPATH": os.pathsep.join(pythonpath),
                "PYTHONUNBUFFERED": "1",
                "ENVIRONMENT": "test",
                "TEST_MODE": "true",
                "POSTGRES_USER": self.database_user,
                "POSTGRES_PASSWORD": self.database_password,
                "POSTGRES_HOST": "127.0.0.1",
                "POSTGRES_PORT": str(self.postgres_port),
                "POSTGRES_DB": self.database,
                "POSTGRES_SSL_MODE": "disable",
                "POSTGRES_ALLOW_INSECURE_TLS": "true",
                "DATABASE_URL": self.database_url,
                "REDIS_URL": self.redis_url,
                "REDIS_HOST": "127.0.0.1",
                "REDIS_PORT": str(self.valkey_port),
                "LOG_STREAM_NAME": self.stream_name,
                "INGEST_API_KEY": "",
                # This key is intentionally not used.  It keeps unknown and
                # revoked DB-key paths at HTTP 403 instead of a configuration
                # 503 while retaining the ownerless-key denial contract.
                "INGEST_API_KEYS": f"fallback-tenant:{self.fallback_key}",
                "JWT_SECRET_KEY": self.jwt_secret,
                "ENCRYPTION_KEY": ENCRYPTION_KEY,
                "METRICS_TOKEN": f"ls06b-metrics-{self.run_id}",
                "FRONTEND_URL": "http://127.0.0.1",
                "RUN_EMBEDDED_WORKERS": "false",
                "RUN_WEBHOOK_WORKER_IN_LIFESPAN": "false",
                "RUN_ARCHIVE_WORKER_IN_LIFESPAN": "false",
                "DRAIN3_STATE_BACKEND": "file",
                "DRAIN3_STATE_PATH": str(state_path),
                "DRAIN3_BATCH_SIZE": "500",
                "DRAIN3_FLUSH_INTERVAL_SECONDS": "5.0",
                "GRAPH_SCORING_ENABLED": "false",
                "SSO_UNMAPPED_PROVIDER_POLICY": "deny",
            }
        )
        return environment


@dataclass(slots=True)
class ManagedProcess:
    """One exact child process and its private bounded log file."""

    process: subprocess.Popen[str]
    log_path: Path
    _log_file: Any = field(repr=False)

    @classmethod
    def start(
        cls,
        args: list[str],
        *,
        cwd: Path,
        environment: dict[str, str],
        log_path: Path,
    ) -> "ManagedProcess":
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("w", encoding="utf-8")
        process = subprocess.Popen(
            args,
            cwd=cwd,
            env=environment,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        return cls(process=process, log_path=log_path, _log_file=log_file)

    @property
    def pid(self) -> int:
        return self.process.pid

    def poll(self) -> int | None:
        return self.process.poll()

    def kill(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                # The target is this exact child process; do not broaden the
                # termination scope to a process tree or unrelated service.
                pass
        self._close_log()

    def terminate(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=15)
        self._close_log()

    def log_text(self) -> str:
        try:
            self._log_file.flush()
        except Exception:
            pass
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _close_log(self) -> None:
        if not self._log_file.closed:
            self._log_file.close()


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def make_config() -> HarnessConfig:
    run_id = uuid4().hex[:12]
    return HarnessConfig(
        run_id=run_id,
        compose_project=f"logsentinel-06b-{run_id}",
        database=f"ls06b_{run_id}",
        database_user="logsentinel",
        database_password=f"ls06b-db-{secrets.token_urlsafe(18)}",
        postgres_port=_free_loopback_port(),
        valkey_port=_free_loopback_port(),
        api_port=_free_loopback_port(),
        stream_name=f"logs:stream:06b:{run_id}",
        fallback_key=f"ls06b-fallback-{secrets.token_urlsafe(24)}",
        jwt_secret=f"ls06b-jwt-{secrets.token_urlsafe(32)}-secret",
    )


def _compose_command(config: HarnessConfig, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        config.compose_project,
        "--file",
        str(COMPOSE_FILE),
        *arguments,
    ]


def _run_compose(
    config: HarnessConfig, *arguments: str, timeout: float = 180.0
) -> None:
    environment = os.environ.copy()
    environment.update(config.compose_environment())
    result = subprocess.run(
        _compose_command(config, *arguments),
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise HarnessFailure(
            f"docker compose {' '.join(arguments)} failed with exit {result.returncode}"
        )


def start_disposable_services(config: HarnessConfig) -> None:
    _run_compose(config, "up", "-d", "--wait", timeout=600.0)


def stop_disposable_services(config: HarnessConfig) -> None:
    try:
        _run_compose(
            config,
            "down",
            "--volumes",
            "--remove-orphans",
            timeout=120.0,
        )
    except (HarnessFailure, OSError, subprocess.SubprocessError):
        # Cleanup is best effort after the exact disposable project has been
        # resolved.  The primary result must retain the original failure.
        pass


def run_migrations(config: HarnessConfig, work_dir: Path) -> None:
    environment = config.application_environment(
        state_path=work_dir / "lifecycle-state.bin"
    )
    result = subprocess.run(
        [sys.executable, "scripts/database_lifecycle.py", "--apply"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180.0,
        check=False,
    )
    if result.returncode != 0:
        raise HarnessFailure("canonical migration lifecycle failed")


async def wait_for(
    label: str,
    predicate: Callable[[], Awaitable[Any]],
    *,
    timeout: float = 30.0,
    interval: float = 0.1,
) -> Any:
    deadline = time.monotonic() + timeout
    last_value: Any = None
    while time.monotonic() < deadline:
        try:
            last_value = await predicate()
        except (OSError, asyncpg.PostgresError, ConnectionError):
            last_value = None
        if last_value:
            return last_value
        await asyncio.sleep(interval)
    raise HarnessFailure(f"timed out waiting for {label}; last={last_value!r}")


async def wait_http_ready(config: HarnessConfig) -> None:
    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{config.api_port}", timeout=3.0
    ) as client:

        async def probe() -> bool:
            try:
                response = await client.get("/readiness")
                return response.status_code == 200
            except httpx.HTTPError:
                return False

        await wait_for("ASGI API readiness", probe, timeout=60.0)


def start_api(config: HarnessConfig, work_dir: Path) -> ManagedProcess:
    environment = config.application_environment(state_path=work_dir / "api-state.bin")
    return ManagedProcess.start(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(config.api_port),
            "--log-level",
            "warning",
        ],
        cwd=REPO_ROOT,
        environment=environment,
        log_path=work_dir / "api.log",
    )


def start_worker(
    config: HarnessConfig,
    work_dir: Path,
    instance: str,
    *,
    phase: str = "",
    target_window: str = "",
) -> ManagedProcess:
    state_path = work_dir / f"drain-state-{instance}.bin"
    environment = config.application_environment(state_path=state_path)
    environment["WORKER_INSTANCE_ID"] = instance
    environment["LS06B_WORKER_REPORT_KEY"] = f"ls06b:report:{config.run_id}:{instance}"
    environment["LS06B_RECOVERY_IDLE_MS"] = "1000"
    if instance.startswith("feature-replay"):
        environment["LS06B_FEATURE_DEBUG_PATH"] = str(
            work_dir / "feature-replay-debug.jsonl"
        )
    if phase:
        marker = work_dir / f"marker-{instance}.json"
        release = work_dir / f"release-{instance}"
        environment["LS06B_FAULT_PHASE"] = phase
        environment["LS06B_FAULT_MARKER"] = str(marker)
        environment["LS06B_FAULT_RELEASE"] = str(release)
        if target_window:
            environment["LS06B_FAULT_FEATURE_WINDOW_ID"] = target_window
    return ManagedProcess.start(
        [sys.executable, "scripts/integration/remediation_06b_worker.py"],
        cwd=REPO_ROOT,
        environment=environment,
        log_path=work_dir / f"worker-{instance}.log",
    )


async def connect_services(config: HarnessConfig) -> tuple[asyncpg.Pool, Redis]:
    pool = await asyncpg.create_pool(
        user=config.database_user,
        password=config.database_password,
        host="127.0.0.1",
        port=config.postgres_port,
        database=config.database,
        min_size=1,
        max_size=10,
        timeout=5.0,
        command_timeout=30.0,
    )
    redis = Redis.from_url(config.redis_url, decode_responses=True)
    await redis.ping()
    return pool, redis


async def schema_evidence(pool: asyncpg.Pool) -> dict[str, Any]:
    async with pool.acquire() as connection:
        versions = [
            str(row["version"])
            for row in await connection.fetch(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        extension = await connection.fetchval(
            "SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'"
        )
        hypertable = await connection.fetchval(
            """
            SELECT hypertable_name
            FROM timescaledb_information.hypertables
            WHERE hypertable_schema = 'public' AND hypertable_name = 'logs'
            """
        )
        required = {
            "pipeline_ledger",
            "pipeline_outbox",
            "pipeline_feature_inputs",
            "ingestion_api_keys",
            "tenant_memberships",
            "feature_windows",
            "anomaly_events",
            "tracking_loops",
            "incidents",
        }
        rows = await connection.fetch(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public' AND tablename = ANY($1::text[])
            """,
            list(required),
        )
        tables = {str(row["tablename"]) for row in rows}
    expected_tail = {
        "20260911_0004_legacy_logsentinel_tenant_bridge",
        "20260911_0005_distributed_correctness",
        "20260911_0006_durable_webhook_delivery",
        "20260912_0007_application_release_gates",
        "20260912_0008_tenant_authority",
        "20260912_0009_pipeline_feature_durability",
        "20260913_0010_per_user_data_ownership",
    }
    if extension != 1 or hypertable != "logs":
        raise HarnessFailure("TimescaleDB extension/hypertable readiness failed")
    if not expected_tail.issubset(set(versions)):
        raise HarnessFailure("current 0004-0010 migration chain is incomplete")
    if not required.issubset(tables):
        raise HarnessFailure("required durable/auth tables are missing")
    return {
        "timescaledb_extension": True,
        "logs_hypertable": True,
        "migration_versions": versions,
        "required_tables": sorted(tables),
    }


async def provision_fixtures(pool: asyncpg.Pool, run_id: str) -> dict[str, Any]:
    tenants = {
        "a": f"06b-tenant-a-{run_id}",
        "b": f"06b-tenant-b-{run_id}",
        "c": f"06b-tenant-c-{run_id}",
    }
    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.executemany(
                "INSERT INTO tenants (id, name, status) VALUES ($1, $2, $3)",
                [
                    (tenants["a"], "Remediation 06B Tenant A", "active"),
                    (tenants["b"], "Remediation 06B Tenant B", "active"),
                    (tenants["c"], "Remediation 06B Tenant C", "suspended"),
                ],
            )
            user_specs = [
                ("a1", "a1", tenants["a"], "active", "admin"),
                ("a2", "a2", tenants["a"], "active", "viewer"),
                ("a3", "a3", tenants["a"], "suspended", "viewer"),
                ("a4", "a4", tenants["a"], "active", "viewer"),
                ("b1", "b1", tenants["b"], "active", "viewer"),
                ("c1", "c1", tenants["c"], "active", "viewer"),
            ]
            users: dict[str, int] = {}
            for label, local, tenant_id, status, role in user_specs:
                user_id = await connection.fetchval(
                    """
                    INSERT INTO users (email, tenant_id, status, role, email_verified_at)
                    VALUES ($1, $2, $3, $4, NOW())
                    RETURNING id
                    """,
                    f"06b-{local}-{run_id}@example.test",
                    tenant_id,
                    status,
                    role,
                )
                users[label] = int(user_id)
            memberships = [
                (tenants["a"], users["a1"], "admin", "active"),
                (tenants["a"], users["a2"], "viewer", "active"),
                (tenants["a"], users["a3"], "viewer", "active"),
                # A4 has only a membership in the wrong tenant.
                (tenants["b"], users["a4"], "viewer", "active"),
                (tenants["b"], users["b1"], "viewer", "active"),
                # This deliberately inconsistent membership makes the key
                # owner/tenant check data-bearing rather than source-only.
                (tenants["a"], users["b1"], "viewer", "active"),
                (tenants["c"], users["c1"], "viewer", "active"),
            ]
            await connection.executemany(
                """
                INSERT INTO tenant_memberships (tenant_id, user_id, role, status)
                VALUES ($1, $2, $3, $4)
                """,
                memberships,
            )

    return {"tenant_ids": tenants, "user_ids": users}


async def create_key(
    pool: asyncpg.Pool,
    *,
    label: str,
    tenant_id: str,
    user_id: int,
    expires_at: datetime | None,
    scopes: list[str] | None,
    revoked: bool = False,
) -> dict[str, Any]:
    raw_key = f"ls06b-{label}-{secrets.token_urlsafe(24)}"
    revoked_at = datetime.now(timezone.utc) if revoked else None
    scopes_json = json.dumps(scopes) if scopes is not None else None
    async with pool.acquire() as connection:
        key_id = await connection.fetchval(
            """
            INSERT INTO ingestion_api_keys
                (tenant_id, user_id, key_prefix, key_hash, expires_at, scopes, revoked_at)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
            RETURNING id
            """,
            tenant_id,
            user_id,
            raw_key[:16],
            hashlib.sha256(raw_key.encode("utf-8")).hexdigest(),
            expires_at,
            scopes_json,
            revoked_at,
        )
    return {
        "id": int(key_id),
        "raw": raw_key,
        "tenant_id": tenant_id,
        "user_id": user_id,
    }


async def provision_keys(
    pool: asyncpg.Pool, fixtures: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    tenants = fixtures["tenant_ids"]
    users = fixtures["user_ids"]
    now = datetime.now(timezone.utc)
    return {
        "valid_a1": await create_key(
            pool,
            label="valid-a1",
            tenant_id=tenants["a"],
            user_id=users["a1"],
            expires_at=now + timedelta(days=30),
            scopes=["logs:ingest"],
        ),
        "valid_a2": await create_key(
            pool,
            label="valid-a2",
            tenant_id=tenants["a"],
            user_id=users["a2"],
            expires_at=now + timedelta(days=30),
            scopes=["logs:ingest"],
        ),
        "expired": await create_key(
            pool,
            label="expired",
            tenant_id=tenants["a"],
            user_id=users["a1"],
            expires_at=now - timedelta(seconds=1),
            scopes=["logs:ingest"],
        ),
        "revoked": await create_key(
            pool,
            label="revoked",
            tenant_id=tenants["a"],
            user_id=users["a1"],
            expires_at=now + timedelta(days=30),
            scopes=["logs:ingest"],
            revoked=True,
        ),
        "unscoped": await create_key(
            pool,
            label="unscoped",
            tenant_id=tenants["a"],
            user_id=users["a1"],
            expires_at=now + timedelta(days=30),
            scopes=["logs:read"],
        ),
        "no_expiry": await create_key(
            pool,
            label="no-expiry",
            tenant_id=tenants["a"],
            user_id=users["a1"],
            expires_at=None,
            scopes=["logs:ingest"],
        ),
        "inactive_owner": await create_key(
            pool,
            label="inactive-owner",
            tenant_id=tenants["a"],
            user_id=users["a3"],
            expires_at=now + timedelta(days=30),
            scopes=["logs:ingest"],
        ),
        "inactive_tenant": await create_key(
            pool,
            label="inactive-tenant",
            tenant_id=tenants["c"],
            user_id=users["c1"],
            expires_at=now + timedelta(days=30),
            scopes=["logs:ingest"],
        ),
        "wrong_membership": await create_key(
            pool,
            label="wrong-membership",
            tenant_id=tenants["a"],
            user_id=users["a4"],
            expires_at=now + timedelta(days=30),
            scopes=["logs:ingest"],
        ),
        "inconsistent_owner": await create_key(
            pool,
            label="inconsistent-owner",
            tenant_id=tenants["a"],
            user_id=users["b1"],
            expires_at=now + timedelta(days=30),
            scopes=["logs:ingest"],
        ),
    }


def access_token(config: HarnessConfig, email: str) -> str:
    now = int(time.time())
    return str(
        jwt.encode(
            {
                "sub": email,
                "exp": now + 900,
                "iat": now,
                "iss": JWT_ISSUER,
                "aud": JWT_AUDIENCE,
                "jti": f"06b-{uuid4().hex}",
            },
            config.jwt_secret,
            algorithm=JWT_ALGORITHM,
        )
    )


def ingest_headers(raw_key: str) -> dict[str, str]:
    return {"X-API-Key": raw_key}


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def iso_now(offset_seconds: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def single_ingest_payload(
    event_id: str,
    *,
    timestamp: str | None = None,
    metadata: dict[str, Any] | None = None,
    root_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source": "remediation-06b",
        "environment": "test",
        "correlation_id": f"06b-correlation-{event_id}",
        "logs": [
            {
                "timestamp": timestamp or iso_now(),
                "service_name": "remediation-06b-service",
                "level": "info",
                "message": f"remediation-06b event {event_id}",
                "event_id": event_id,
                "metadata": metadata or {},
            }
        ],
    }
    if root_fields:
        payload.update(root_fields)
    return payload


async def post_ingest(
    client: httpx.AsyncClient,
    raw_key: str,
    event_id: str,
    *,
    timestamp: str | None = None,
    metadata: dict[str, Any] | None = None,
    root_fields: dict[str, Any] | None = None,
) -> httpx.Response:
    return await client.post(
        "/ingest-log",
        headers=ingest_headers(raw_key),
        json=single_ingest_payload(
            event_id,
            timestamp=timestamp,
            metadata=metadata,
            root_fields=root_fields,
        ),
    )


async def post_ingest_batch(
    client: httpx.AsyncClient,
    raw_key: str,
    event_ids: list[str],
    *,
    timestamp: str,
    service_name: str,
    message_prefix: str,
) -> httpx.Response:
    return await client.post(
        "/ingest-log",
        headers=ingest_headers(raw_key),
        json={
            "source": "remediation-06b",
            "environment": "test",
            "correlation_id": f"06b-correlation-{message_prefix}",
            "logs": [
                {
                    "timestamp": timestamp,
                    "service_name": service_name,
                    "level": "info",
                    "message": f"{message_prefix} {event_id}",
                    "event_id": event_id,
                }
                for event_id in event_ids
            ],
        },
    )


async def post_bulk(
    client: httpx.AsyncClient,
    raw_key: str,
    event_ids: list[str],
    *,
    timestamp: str,
) -> httpx.Response:
    return await client.post(
        "/api/v1/ingest/bulk",
        headers=ingest_headers(raw_key),
        json={
            "logs": [
                {
                    "timestamp": timestamp,
                    "service_name": "remediation-06b-bulk",
                    "level": "INFO",
                    "message": f"remediation-06b competing {event_id}",
                    "event_id": event_id,
                }
                for event_id in event_ids
            ]
        },
    )


async def event_state(
    pool: asyncpg.Pool, tenant_id: str, owner_user_id: int, event_id: str
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        log_count = await connection.fetchval(
            """
            SELECT count(*) FROM logs
            WHERE tenant_id = $1 AND owner_user_id = $2 AND event_id = $3
            """,
            tenant_id,
            owner_user_id,
            event_id,
        )
        ledger_count = await connection.fetchval(
            """
            SELECT count(*) FROM pipeline_ledger
            WHERE tenant_id = $1 AND owner_user_id = $2
              AND stage = 'raw_log' AND event_id = $3
            """,
            tenant_id,
            owner_user_id,
            event_id,
        )
        contribution = await connection.fetchrow(
            """
            SELECT count(*) AS count,
                   COALESCE(array_agg(status ORDER BY id), ARRAY[]::text[]) AS statuses
            FROM pipeline_outbox
            WHERE tenant_id = $1 AND owner_user_id = $2
              AND topic = 'feature_contribution' AND event_id = $3
            """,
            tenant_id,
            owner_user_id,
            event_id,
        )
        input_count = await connection.fetchval(
            """
            SELECT count(*) FROM pipeline_feature_inputs
            WHERE tenant_id = $1 AND owner_user_id = $2 AND event_id = $3
            """,
            tenant_id,
            owner_user_id,
            event_id,
        )
    return {
        "logs": int(log_count or 0),
        "raw_ledger": int(ledger_count or 0),
        "feature_contribution_outbox": int(contribution["count"] or 0),
        "feature_contribution_statuses": [
            str(item) for item in contribution["statuses"]
        ],
        "feature_inputs": int(input_count or 0),
    }


async def wait_event_state(
    pool: asyncpg.Pool,
    tenant_id: str,
    owner_user_id: int,
    event_id: str,
    *,
    timeout: float = 45.0,
) -> dict[str, Any]:
    async def predicate() -> dict[str, Any] | None:
        state = await event_state(pool, tenant_id, owner_user_id, event_id)
        return state if state["logs"] == 1 and state["raw_ledger"] == 1 else None

    return await wait_for(
        f"durable acceptance for {event_id}", predicate, timeout=timeout
    )


async def stream_length(redis: Redis, stream_name: str) -> int:
    return int(await redis.xlen(stream_name))


async def pending_snapshot(redis: Redis, stream_name: str) -> dict[str, Any]:
    summary = await redis.xpending(stream_name, GROUP_NAME)
    rows = await redis.xpending_range(
        stream_name, GROUP_NAME, min="-", max="+", count=100
    )
    return {
        "summary": _json_safe(summary),
        "entries": _json_safe(rows),
    }


async def group_info(redis: Redis, stream_name: str) -> dict[str, Any]:
    groups = await redis.xinfo_groups(stream_name)
    consumers = await redis.xinfo_consumers(stream_name, GROUP_NAME)
    return {"groups": _json_safe(groups), "consumers": _json_safe(consumers)}


async def find_stream_entry(
    redis: Redis, stream_name: str, event_id: str
) -> tuple[str, str, dict[str, Any]]:
    entries = await redis.xrange(stream_name, min="-", max="+")
    for message_id, fields in entries:
        raw_payload = fields.get("payload")
        if not isinstance(raw_payload, str):
            continue
        try:
            envelope = json.loads(raw_payload)
        except json.JSONDecodeError:
            continue
        payload = envelope.get("payload")
        logs = payload.get("logs") if isinstance(payload, dict) else None
        if isinstance(logs, list) and any(
            isinstance(item, dict) and item.get("event_id") == event_id for item in logs
        ):
            return str(message_id), raw_payload, envelope
    raise HarnessFailure(f"stream envelope for {event_id} was not found")


async def worker_heartbeat(redis: Redis, instance: str) -> bool:
    return bool(await redis.get(f"logsentinel:worker-heartbeat:pipeline:{instance}"))


async def report_value(redis: Redis, run_id: str, instance: str) -> int:
    value = await redis.get(f"ls06b:report:{run_id}:{instance}")
    return int(value or 0)


async def ownerless_counts(pool: asyncpg.Pool) -> dict[str, int]:
    tables = [
        "logs",
        "feature_windows",
        "anomaly_events",
        "tracking_loops",
        "incidents",
        "pipeline_ledger",
        "pipeline_feature_inputs",
    ]
    result: dict[str, int] = {}
    async with pool.acquire() as connection:
        for table in tables:
            result[table] = int(
                await connection.fetchval(
                    f"SELECT count(*) FROM {table} WHERE owner_user_id IS NULL"
                )
                or 0
            )
    return result


async def feature_target_state(
    pool: asyncpg.Pool, tenant_id: str, owner_user_id: int, window_id: str
) -> dict[str, int]:
    async with pool.acquire() as connection:
        feature_count = await connection.fetchval(
            """
            SELECT count(*) FROM feature_windows
            WHERE tenant_id = $1 AND owner_user_id = $2 AND window_id = $3
            """,
            tenant_id,
            owner_user_id,
            window_id,
        )
        anomaly_count = await connection.fetchval(
            """
            SELECT count(*) FROM anomaly_events
            WHERE tenant_id = $1 AND owner_user_id = $2 AND window_id = $3
            """,
            tenant_id,
            owner_user_id,
            window_id,
        )
        tracking_count = await connection.fetchval(
            """
            SELECT count(*) FROM tracking_loops
            WHERE tenant_id = $1 AND owner_user_id = $2 AND window_id = $3
            """,
            tenant_id,
            owner_user_id,
            window_id,
        )
    return {
        "feature_windows": int(feature_count or 0),
        "anomaly_events": int(anomaly_count or 0),
        "tracking_loops": int(tracking_count or 0),
    }


async def insert_faultable_feature_work(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    owner_user_id: int,
    window_id: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    prediction = {
        "is_anomaly": True,
        "anomaly_score": 0.95,
        "raw_score": 0.95,
        "severity": "high",
        "model_version": "remediation-06b-test-model",
    }
    feature_names = [
        "log_count",
        "info_count",
        "warning_count",
        "error_count",
        "error_ratio",
        "active_services",
        "unique_templates",
        "dominant_service_count",
        "dominant_template_count",
        "logs_per_second",
        "avg_logs_per_minute",
        "burst_indicator",
    ]
    feature_vector = {
        "window_id": window_id,
        "timestamp": now.isoformat(),
        "window_start": (now - timedelta(seconds=10)).isoformat(),
        "window_end": now.isoformat(),
        "tenant_id": tenant_id,
        "owner_user_id": owner_user_id,
        "log_count": 7,
        "unique_templates": 1,
        "error_count": 7,
        "warning_count": 0,
        "template_frequencies": {"remediation-template": 1.0},
        "template_entropy": 0.0,
        "service_distribution": {"remediation-06b-service": 7},
        "logs_per_second": 0.7,
        "feature_array": [7.0, 0.0, 0.0, 7.0, 1.0, 1.0, 1.0, 7.0, 7.0, 0.7, 42.0, 0.0],
        "feature_names": feature_names,
        "anomaly_prediction": prediction,
        "features": {"log_count": 7.0, "error_count": 7.0},
    }
    outbox_id = f"06b-retry-{uuid4().hex}"
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO pipeline_outbox
                (id, tenant_id, owner_user_id, topic, dedup_key, payload,
                 status, attempts, available_at, event_id, incident_id)
            VALUES ($1, $2, $3, 'feature_window', $4, $5::jsonb,
                    'pending', 0, NOW(), $4, $4)
            """,
            outbox_id,
            tenant_id,
            owner_user_id,
            window_id,
            json.dumps(feature_vector),
        )
    return {"outbox_id": outbox_id, "window_id": window_id}


async def outbox_state(pool: asyncpg.Pool, outbox_id: str) -> dict[str, Any]:
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT status, attempts, available_at, last_error_category, payload
            FROM pipeline_outbox WHERE id = $1
            """,
            outbox_id,
        )
    if row is None:
        raise HarnessFailure(f"outbox row {outbox_id} disappeared")
    payload = row["payload"]
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)
    return {
        "status": str(row["status"]),
        "attempts": int(row["attempts"]),
        "available_at": row["available_at"].isoformat(),
        "last_error_category": row["last_error_category"],
        "payload_empty": payload == {},
    }


async def feature_windows_for_event_time(
    pool: asyncpg.Pool,
    tenant_id: str,
    owner_user_id: int,
    event_time: datetime,
) -> list[dict[str, Any]]:
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT window_id, start_time, end_time, log_count
            FROM feature_windows
            WHERE tenant_id = $1 AND owner_user_id = $2
              AND start_time <= $3 AND end_time > $3
            ORDER BY start_time, window_id
            """,
            tenant_id,
            owner_user_id,
            event_time,
        )
    return [
        {
            "window_id": str(row["window_id"]),
            "start_time": row["start_time"].isoformat(),
            "end_time": row["end_time"].isoformat(),
            "log_count": int(row["log_count"]),
        }
        for row in rows
    ]


async def run_scenario(config: HarnessConfig, work_dir: Path) -> dict[str, Any]:
    pool, redis = await connect_services(config)
    processes: list[ManagedProcess] = []
    evidence: dict[str, Any] = {
        "status": "PASS",
        "run_id": config.run_id,
        "compose_project": config.compose_project,
        "images": {
            "postgres": "timescale/timescaledb@sha256:4e459e217f00cbb09920c34d245501e63427e6767a495de57ce76823ff280f12",
            "valkey": "valkey/valkey@sha256:752ba000a58bc8925d11ee863a39b5225617cd2cb3dbc9ec8ac65268b65ebb6d",
        },
        "loopback_ports": {
            "postgres": config.postgres_port,
            "valkey": config.valkey_port,
            "api": config.api_port,
        },
    }
    api: ManagedProcess | None = None
    try:
        evidence["schema"] = await schema_evidence(pool)
        fixtures = await provision_fixtures(pool, config.run_id)
        keys = await provision_keys(pool, fixtures)
        tenant_a = fixtures["tenant_ids"]["a"]
        tenant_b = fixtures["tenant_ids"]["b"]
        owner_a1 = fixtures["user_ids"]["a1"]
        owner_a2 = fixtures["user_ids"]["a2"]
        api = start_api(config, work_dir)
        processes.append(api)
        await wait_http_ready(config)

        token_a1 = access_token(config, f"06b-a1-{config.run_id}@example.test")
        token_a2 = access_token(config, f"06b-a2-{config.run_id}@example.test")
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{config.api_port}",
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_connections=40),
        ) as client:
            auth_worker = start_worker(config, work_dir, "auth")
            processes.append(auth_worker)
            await wait_for(
                "auth worker heartbeat",
                lambda: worker_heartbeat(redis, "auth"),
                timeout=60.0,
            )

            valid_a1_response = await post_ingest(
                client, keys["valid_a1"]["raw"], "SEC-A1"
            )
            if valid_a1_response.status_code != 202:
                raise HarnessFailure("valid active scoped key was not accepted")
            valid_a1_state = await wait_event_state(pool, tenant_a, owner_a1, "SEC-A1")
            stream_id_a1, _, envelope_a1 = await find_stream_entry(
                redis, config.stream_name, "SEC-A1"
            )
            if (
                envelope_a1.get("tenant_id") != tenant_a
                or envelope_a1.get("owner_user_id") != owner_a1
            ):
                raise HarnessFailure(
                    "accepted stream envelope lost exact key DataScope"
                )

            valid_a2_response = await post_ingest(
                client, keys["valid_a2"]["raw"], "SEC-A2"
            )
            if valid_a2_response.status_code != 202:
                raise HarnessFailure("second valid scoped key was not accepted")
            valid_a2_state = await wait_event_state(pool, tenant_a, owner_a2, "SEC-A2")

            spoof_results: dict[str, Any] = {}
            for field, value in (
                ("owner_user_id", owner_a2),
                ("user_id", owner_a2),
                ("tenant_id", tenant_b),
            ):
                event_id = f"SEC-ROOT-{field}"
                response = await post_ingest(
                    client,
                    keys["valid_a1"]["raw"],
                    event_id,
                    root_fields={field: value},
                )
                if response.status_code == 202:
                    state = await wait_event_state(pool, tenant_a, owner_a1, event_id)
                    if state["logs"] != 1:
                        raise HarnessFailure(
                            f"root spoof {field} was not durably owned by A1"
                        )
                    spoof_results[field] = {
                        "status_code": response.status_code,
                        "result": "authoritative_a1_persisted",
                        "state": state,
                    }
                elif response.status_code in {400, 401, 403, 422}:
                    spoof_results[field] = {
                        "status_code": response.status_code,
                        "result": "rejected",
                    }
                else:
                    raise HarnessFailure(f"unexpected root spoof response for {field}")

            nested_results: dict[str, int] = {}
            for index, (field, value) in enumerate(
                (
                    ("owner_user_id", owner_a2),
                    ("user_id", owner_a2),
                    ("tenant_id", tenant_b),
                )
            ):
                before = await stream_length(redis, config.stream_name)
                response = await post_ingest(
                    client,
                    keys["valid_a1"]["raw"],
                    f"SEC-NESTED-{index}",
                    metadata={field: value},
                )
                after = await stream_length(redis, config.stream_name)
                if response.status_code != 422 or after != before:
                    raise HarnessFailure(
                        f"nested spoof {field} was not rejected before enqueue"
                    )
                nested_results[field] = response.status_code

            invalid_key_cases = {
                "expired": keys["expired"],
                "revoked": keys["revoked"],
                "unscoped": keys["unscoped"],
                "no_expiry": keys["no_expiry"],
                "inactive_owner": keys["inactive_owner"],
                "inactive_tenant": keys["inactive_tenant"],
                "wrong_membership": keys["wrong_membership"],
                "inconsistent_owner": keys["inconsistent_owner"],
            }
            invalid_results: dict[str, Any] = {}
            for index, (label, key) in enumerate(invalid_key_cases.items()):
                before = await stream_length(redis, config.stream_name)
                response = await post_ingest(client, key["raw"], f"SEC-DENIED-{index}")
                after = await stream_length(redis, config.stream_name)
                if response.status_code != 403 or after != before:
                    raise HarnessFailure(f"API-key denial matrix failed for {label}")
                invalid_results[label] = {
                    "status_code": response.status_code,
                    "stream_delta": after - before,
                }

            # A cross-tenant destination attempt is represented by the only
            # client-controlled tenant-shaped field accepted by the payload:
            # nested metadata.  It must fail before XADD and cannot create B
            # rows or durable work.
            before_cross = await stream_length(redis, config.stream_name)
            cross_response = await post_ingest(
                client,
                keys["valid_a1"]["raw"],
                "SEC-CROSS-TENANT",
                metadata={"tenant_id": tenant_b},
            )
            after_cross = await stream_length(redis, config.stream_name)
            if cross_response.status_code != 422 or after_cross != before_cross:
                raise HarnessFailure(
                    "cross-tenant ingestion was not denied before enqueue"
                )
            async with pool.acquire() as connection:
                cross_b_rows = await connection.fetchval(
                    "SELECT count(*) FROM logs WHERE tenant_id = $1 OR event_id = $2",
                    tenant_b,
                    "SEC-CROSS-TENANT",
                )
            if int(cross_b_rows or 0) != 0:
                raise HarnessFailure("cross-tenant denial created Tenant B data")

            # Verify current-state transitions, not only prebuilt invalid rows.
            async with pool.acquire() as connection:
                await connection.execute(
                    "UPDATE ingestion_api_keys SET revoked_at = NOW() WHERE id = $1",
                    keys["valid_a2"]["id"],
                )
            before_revoke = await stream_length(redis, config.stream_name)
            revoke_response = await post_ingest(
                client, keys["valid_a2"]["raw"], "SEC-REVOKE-TRANSITION"
            )
            after_revoke = await stream_length(redis, config.stream_name)
            if revoke_response.status_code != 403 or after_revoke != before_revoke:
                raise HarnessFailure("revoking a valid key did not revoke ingestion")
            async with pool.acquire() as connection:
                await connection.execute(
                    "UPDATE ingestion_api_keys SET revoked_at = NULL WHERE id = $1",
                    keys["valid_a2"]["id"],
                )
            async with pool.acquire() as connection:
                await connection.execute(
                    "UPDATE users SET status = 'suspended' WHERE id = $1", owner_a2
                )
            owner_suspend_response = await post_ingest(
                client, keys["valid_a2"]["raw"], "SEC-SUSPEND-OWNER"
            )
            if owner_suspend_response.status_code != 403:
                raise HarnessFailure(
                    "suspending a valid owner did not revoke ingestion"
                )
            async with pool.acquire() as connection:
                await connection.execute(
                    "UPDATE users SET status = 'active' WHERE id = $1", owner_a2
                )
                await connection.execute(
                    "UPDATE tenant_memberships SET status = 'suspended' WHERE tenant_id = $1 AND user_id = $2",
                    tenant_a,
                    owner_a2,
                )
            membership_suspend_response = await post_ingest(
                client, keys["valid_a2"]["raw"], "SEC-SUSPEND-MEMBERSHIP"
            )
            if membership_suspend_response.status_code != 403:
                raise HarnessFailure(
                    "suspending a valid membership did not revoke ingestion"
                )
            async with pool.acquire() as connection:
                await connection.execute(
                    "UPDATE tenant_memberships SET status = 'active' WHERE tenant_id = $1 AND user_id = $2",
                    tenant_a,
                    owner_a2,
                )
                await connection.execute(
                    "UPDATE tenants SET status = 'suspended' WHERE id = $1", tenant_a
                )
            tenant_suspend_response = await post_ingest(
                client, keys["valid_a1"]["raw"], "SEC-SUSPEND-TENANT"
            )
            if tenant_suspend_response.status_code != 403:
                raise HarnessFailure(
                    "suspending a valid tenant did not revoke ingestion"
                )
            async with pool.acquire() as connection:
                await connection.execute(
                    "UPDATE tenants SET status = 'active' WHERE id = $1", tenant_a
                )

            # Exercise the actual API-key creation endpoint and its PostgreSQL
            # advisory transaction lock concurrently.  Plaintext responses
            # are consumed only for cleanup accounting and never recorded.
            create_results = await asyncio.gather(
                *[
                    client.post(
                        "/api/auth/api-key?expires_in_days=90",
                        headers=auth_headers(token_a1),
                    )
                    for _ in range(30)
                ]
            )
            create_statuses = [response.status_code for response in create_results]
            if any(status not in {200, 409} for status in create_statuses):
                raise HarnessFailure(
                    "concurrent API-key creation returned an unexpected error"
                )
            async with pool.acquire() as connection:
                active_key_count = await connection.fetchval(
                    """
                    SELECT count(*) FROM ingestion_api_keys
                    WHERE tenant_id = $1 AND user_id = $2
                      AND revoked_at IS NULL AND expires_at > NOW()
                    """,
                    tenant_a,
                    owner_a1,
                )
            if int(active_key_count or 0) > 25:
                raise HarnessFailure(
                    "concurrent API-key creation bypassed the active limit"
                )

            # Admin role is not an operational-data wildcard.
            a1_recent = await client.get(
                "/api/v1/logs/recent?limit=200", headers=auth_headers(token_a1)
            )
            a2_recent = await client.get(
                "/api/v1/logs/recent?limit=200", headers=auth_headers(token_a2)
            )
            if a1_recent.status_code != 200 or a2_recent.status_code != 200:
                raise HarnessFailure("owner-scoped log read did not remain available")
            a1_messages = [
                str(row.get("raw_message", "")) for row in a1_recent.json()["logs"]
            ]
            a2_messages = [
                str(row.get("raw_message", "")) for row in a2_recent.json()["logs"]
            ]
            if any("SEC-A2" in message for message in a1_messages):
                raise HarnessFailure("admin A1 read another owner's operational log")
            if not any("SEC-A1" in message for message in a1_messages):
                raise HarnessFailure("A1 could not read its own operational log")
            if any("SEC-A1" in message for message in a2_messages):
                raise HarnessFailure("A2 read A1's operational log")
            if not any("SEC-A2" in message for message in a2_messages):
                raise HarnessFailure("A2 could not read its own operational log")

            async with pool.acquire() as connection:
                last_used = await connection.fetchval(
                    "SELECT last_used_at IS NOT NULL FROM ingestion_api_keys WHERE id = $1",
                    keys["valid_a1"]["id"],
                )
            if last_used is not True:
                raise HarnessFailure(
                    "valid API-key use was not reflected in real DB state"
                )

            evidence["authorization"] = {
                "valid_a1": {
                    "status_code": valid_a1_response.status_code,
                    "stream_id": stream_id_a1,
                    "state": valid_a1_state,
                },
                "valid_a2": {
                    "status_code": valid_a2_response.status_code,
                    "state": valid_a2_state,
                },
                "accepted_envelope_scope": {
                    "tenant_id": tenant_a,
                    "owner_user_id": owner_a1,
                },
                "ownership_spoofing": spoof_results,
                "nested_spoofing": nested_results,
                "denial_matrix": invalid_results,
                "cross_tenant": {
                    "status_code": cross_response.status_code,
                    "stream_delta": after_cross - before_cross,
                    "tenant_b_rows": int(cross_b_rows or 0),
                },
                "state_transitions": {
                    "key_revoked": {
                        "status_code": revoke_response.status_code,
                        "stream_delta": after_revoke - before_revoke,
                    },
                    "owner_suspended": owner_suspend_response.status_code,
                    "membership_suspended": membership_suspend_response.status_code,
                    "tenant_suspended": tenant_suspend_response.status_code,
                },
                "concurrent_key_creation": {
                    "request_count": len(create_statuses),
                    "status_counts": {
                        str(status): create_statuses.count(status)
                        for status in sorted(set(create_statuses))
                    },
                    "active_key_count_after": int(active_key_count or 0),
                    "limit": 25,
                },
                "owner_read_isolation": {
                    "admin_a1_sees_a2": any(
                        "SEC-A2" in message for message in a1_messages
                    ),
                    "a2_sees_a1": any("SEC-A1" in message for message in a2_messages),
                    "a1_own_log": any("SEC-A1" in message for message in a1_messages),
                    "a2_own_log": any("SEC-A2" in message for message in a2_messages),
                },
                "ownerless_operational_rows": await ownerless_counts(pool),
            }

            # Establish baseline through the actual stream consumer before
            # injecting failure windows.
            baseline_ids = [f"E{index}" for index in range(1, 7)]
            # Keep the baseline after the already-buffered authorization
            # events.  The live extractor's cursor is monotonic in event time;
            # placing this deterministic batch in the near future prevents an
            # intentionally old fixture from being outside that cursor while
            # still requiring the real worker to close the window.
            baseline_timestamp = iso_now(15)
            baseline_response = await client.post(
                "/ingest-log",
                headers=ingest_headers(keys["valid_a1"]["raw"]),
                json={
                    "source": "remediation-06b-baseline",
                    "environment": "test",
                    "correlation_id": "06b-baseline",
                    "logs": [
                        {
                            "timestamp": baseline_timestamp,
                            "service_name": "remediation-06b-baseline",
                            "level": "info",
                            "message": f"remediation-06b baseline {event_id}",
                            "event_id": event_id,
                        }
                        for event_id in baseline_ids
                    ],
                },
            )
            if baseline_response.status_code != 202:
                raise HarnessFailure(
                    "baseline deterministic event batch was not accepted"
                )
            for event_id in baseline_ids:
                await wait_event_state(pool, tenant_a, owner_a1, event_id)

            async def baseline_inputs_ready() -> bool:
                async with pool.acquire() as connection:
                    count = await connection.fetchval(
                        """
                        SELECT count(*) FROM pipeline_feature_inputs
                        WHERE tenant_id = $1 AND owner_user_id = $2
                          AND event_id = ANY($3::text[])
                        """,
                        tenant_a,
                        owner_a1,
                        baseline_ids,
                    )
                return int(count or 0) == len(baseline_ids)

            await wait_for(
                "baseline feature inputs", baseline_inputs_ready, timeout=60.0
            )

            async def baseline_window_ready() -> list[dict[str, Any]]:
                rows = await feature_windows_for_event_time(
                    pool, tenant_a, owner_a1, datetime.fromisoformat(baseline_timestamp)
                )
                return rows

            baseline_windows = await wait_for(
                "baseline feature window", baseline_window_ready, timeout=60.0
            )
            baseline_states = {
                event_id: await event_state(pool, tenant_a, owner_a1, event_id)
                for event_id in baseline_ids
            }
            evidence["pipeline_baseline"] = {
                "event_ids": baseline_ids,
                "accepted_batch_status": baseline_response.status_code,
                "states": baseline_states,
                "feature_windows_for_timestamp": baseline_windows,
                "pending_after_healthy_flow": await pending_snapshot(
                    redis, config.stream_name
                ),
                "group_after_healthy_flow": await group_info(redis, config.stream_name),
            }

            auth_worker.kill()
            processes.remove(auth_worker)

            # Crash window 1: the consumer has the entry in its PEL but the
            # PostgreSQL acceptance transaction has not started/committed.
            before_worker = start_worker(
                config,
                work_dir,
                "crash-before-commit",
                phase="before_db_commit",
            )
            processes.append(before_worker)
            await wait_for(
                "before-commit worker heartbeat",
                lambda: worker_heartbeat(redis, "crash-before-commit"),
                timeout=60.0,
            )
            before_response = await post_ingest(
                client, keys["valid_a1"]["raw"], "E-BEFORE", timestamp=iso_now(-2)
            )
            if before_response.status_code != 202:
                raise HarnessFailure(
                    "pre-commit fault event was not accepted to stream"
                )
            before_marker = work_dir / "marker-crash-before-commit.json"
            await wait_for(
                "pre-commit fault marker",
                lambda: _file_exists(before_marker),
                timeout=30.0,
            )
            before_pending = await pending_snapshot(redis, config.stream_name)
            before_state = await event_state(pool, tenant_a, owner_a1, "E-BEFORE")
            if int(before_pending["summary"]["pending"]) != 1:
                raise HarnessFailure(
                    "pre-commit crash did not leave one pending stream entry"
                )
            if any(
                before_state[key] != 0
                for key in ("logs", "raw_ledger", "feature_contribution_outbox")
            ):
                raise HarnessFailure(
                    "pre-commit crash left partially committed durable acceptance"
                )
            before_worker.kill()
            processes.remove(before_worker)

            recovery_before = start_worker(config, work_dir, "recover-before")
            processes.append(recovery_before)
            await wait_for(
                "pre-commit recovery worker heartbeat",
                lambda: worker_heartbeat(redis, "recover-before"),
                timeout=60.0,
            )
            recovered_before_state = await wait_event_state(
                pool, tenant_a, owner_a1, "E-BEFORE", timeout=55.0
            )
            if recovered_before_state["feature_contribution_outbox"] != 1:
                raise HarnessFailure(
                    "pre-commit recovery did not create exactly one downstream registration"
                )
            await wait_for(
                "pre-commit pending entry reclaimed and ACKed",
                lambda: _pending_empty(redis, config.stream_name),
                timeout=55.0,
            )
            before_log = recovery_before.log_text()
            if "Auto-claimed" not in before_log:
                raise HarnessFailure(
                    "pre-commit recovery did not evidence current XAUTOCLAIM path"
                )
            before_after_pending = await pending_snapshot(redis, config.stream_name)
            recovery_before.kill()
            processes.remove(recovery_before)
            evidence["crash_before_commit"] = {
                "pre_crash_state": before_state,
                "pending_before_restart": before_pending,
                "post_recovery_state": recovered_before_state,
                "pending_after_recovery": before_after_pending,
                "recovery_log_confirmed_xautoclaim": True,
            }

            # Crash window 2: COMMIT has returned, but the exact worker is
            # held immediately before its XACK call.
            after_worker = start_worker(
                config,
                work_dir,
                "crash-after-commit",
                phase="after_db_commit_before_xack",
            )
            processes.append(after_worker)
            await wait_for(
                "after-commit worker heartbeat",
                lambda: worker_heartbeat(redis, "crash-after-commit"),
                timeout=60.0,
            )
            after_response = await post_ingest(
                client, keys["valid_a1"]["raw"], "E-AFTER", timestamp=iso_now(-2)
            )
            if after_response.status_code != 202:
                raise HarnessFailure(
                    "post-commit fault event was not accepted to stream"
                )
            after_marker = work_dir / "marker-crash-after-commit.json"
            await wait_for(
                "post-commit fault marker",
                lambda: _file_exists(after_marker),
                timeout=30.0,
            )
            after_pending = await pending_snapshot(redis, config.stream_name)
            after_pre_state = await event_state(pool, tenant_a, owner_a1, "E-AFTER")
            if int(after_pending["summary"]["pending"]) != 1:
                raise HarnessFailure(
                    "post-commit crash did not leave one pending stream entry"
                )
            if (
                after_pre_state["logs"] != 1
                or after_pre_state["raw_ledger"] != 1
                or after_pre_state["feature_contribution_outbox"] != 1
            ):
                raise HarnessFailure(
                    "post-commit crash did not preserve committed raw acceptance and downstream registration"
                )
            (
                original_after_id,
                original_after_payload,
                original_after_envelope,
            ) = await find_stream_entry(redis, config.stream_name, "E-AFTER")
            after_worker.kill()
            processes.remove(after_worker)

            recovery_after = start_worker(config, work_dir, "recover-after")
            processes.append(recovery_after)
            await wait_for(
                "post-commit recovery worker heartbeat",
                lambda: worker_heartbeat(redis, "recover-after"),
                timeout=60.0,
            )
            recovered_after_state = await wait_event_state(
                pool, tenant_a, owner_a1, "E-AFTER", timeout=55.0
            )
            await wait_for(
                "post-commit pending entry reclaimed and ACKed",
                lambda: _pending_empty(redis, config.stream_name),
                timeout=55.0,
            )
            duplicate_stream_id = await redis.xadd(
                config.stream_name, {"payload": original_after_payload}
            )
            duplicate_state = await wait_event_state(
                pool, tenant_a, owner_a1, "E-AFTER", timeout=45.0
            )
            await wait_for(
                "duplicate redelivery ACK",
                lambda: _pending_empty(redis, config.stream_name),
                timeout=45.0,
            )
            after_final_pending = await pending_snapshot(redis, config.stream_name)
            if (
                duplicate_state["logs"] != 1
                or duplicate_state["raw_ledger"] != 1
                or duplicate_state["feature_contribution_outbox"] != 1
            ):
                raise HarnessFailure(
                    "duplicate redelivery created a second logical pipeline result"
                )
            recovery_after.kill()
            processes.remove(recovery_after)
            evidence["crash_after_commit_before_xack"] = {
                "pre_crash_state": after_pre_state,
                "pending_after_commit_before_xack": after_pending,
                "original_stream_id": original_after_id,
                "original_envelope_scope": {
                    "tenant_id": original_after_envelope.get("tenant_id"),
                    "owner_user_id": original_after_envelope.get("owner_user_id"),
                },
                "post_recovery_state": recovered_after_state,
                "duplicate_stream_id": str(duplicate_stream_id),
                "post_duplicate_state": duplicate_state,
                "pending_after_duplicate": after_final_pending,
                "logical_raw_count": duplicate_state["logs"],
                "logical_raw_ledger_count": duplicate_state["raw_ledger"],
                "logical_feature_contribution_count": duplicate_state[
                    "feature_contribution_outbox"
                ],
            }

            # Two current worker processes share the real consumer group and
            # durable lease tables.  The bulk endpoint creates >500 entries,
            # forcing the current count=500 reader to involve both consumers.
            competing_a = start_worker(config, work_dir, "competing-a")
            competing_b = start_worker(config, work_dir, "competing-b")
            processes.extend([competing_a, competing_b])
            await asyncio.gather(
                wait_for(
                    "competing worker A heartbeat",
                    lambda: worker_heartbeat(redis, "competing-a"),
                    timeout=60.0,
                ),
                wait_for(
                    "competing worker B heartbeat",
                    lambda: worker_heartbeat(redis, "competing-b"),
                    timeout=60.0,
                ),
            )
            competing_ids = [f"C{index:03d}" for index in range(520)]
            competing_start_a = await report_value(redis, config.run_id, "competing-a")
            competing_start_b = await report_value(redis, config.run_id, "competing-b")
            competing_response = await post_bulk(
                client,
                keys["valid_a1"]["raw"],
                competing_ids,
                timestamp=iso_now(-12),
            )
            if competing_response.status_code != 202:
                raise HarnessFailure("competing-worker bulk ingestion was not accepted")

            async def competing_raw_ready() -> bool:
                async with pool.acquire() as connection:
                    count = await connection.fetchval(
                        """
                        SELECT count(*) FROM logs
                        WHERE tenant_id = $1 AND owner_user_id = $2
                          AND event_id = ANY($3::text[])
                        """,
                        tenant_a,
                        owner_a1,
                        competing_ids,
                    )
                return int(count or 0) == len(competing_ids)

            await wait_for(
                "competing-worker raw convergence", competing_raw_ready, timeout=120.0
            )
            await wait_for(
                "competing-worker stream ACK convergence",
                lambda: _pending_empty(redis, config.stream_name),
                timeout=120.0,
            )

            async def competing_reports_ready() -> bool:
                a = await report_value(redis, config.run_id, "competing-a")
                b = await report_value(redis, config.run_id, "competing-b")
                return (
                    a - competing_start_a + b - competing_start_b >= len(competing_ids)
                    and a > competing_start_a
                    and b > competing_start_b
                )

            await wait_for(
                "both competing workers process entries",
                competing_reports_ready,
                timeout=120.0,
            )
            competing_report_a = (
                await report_value(redis, config.run_id, "competing-a")
                - competing_start_a
            )
            competing_report_b = (
                await report_value(redis, config.run_id, "competing-b")
                - competing_start_b
            )
            competing_a.kill()
            competing_b.kill()
            processes.remove(competing_a)
            processes.remove(competing_b)
            evidence["competing_workers"] = {
                "bulk_count": len(competing_ids),
                "accepted_status": competing_response.status_code,
                "worker_a_processed_delta": competing_report_a,
                "worker_b_processed_delta": competing_report_b,
                "raw_rows": len(competing_ids),
                "pending_after": await pending_snapshot(redis, config.stream_name),
            }

            # Fault after feature/anomaly inserts but before the outbox handler
            # can be marked complete.  The savepoint must roll back all three
            # derived rows, then a retry must converge exactly once.
            retry_window_id = f"06b-feature-retry-{config.run_id}"
            retry_work = await insert_faultable_feature_work(
                pool,
                tenant_id=tenant_a,
                owner_user_id=owner_a1,
                window_id=retry_window_id,
            )
            partial_worker = start_worker(
                config,
                work_dir,
                "partial-feature",
                phase="feature_partial_stage",
                target_window=retry_window_id,
            )
            processes.append(partial_worker)
            await wait_for(
                "partial-feature worker heartbeat",
                lambda: worker_heartbeat(redis, "partial-feature"),
                timeout=60.0,
            )
            partial_marker = work_dir / "marker-partial-feature.json"
            await wait_for(
                "partial feature-stage fault marker",
                lambda: _file_exists(partial_marker),
                timeout=60.0,
            )

            async def retry_state_visible() -> dict[str, Any] | None:
                state = await outbox_state(pool, retry_work["outbox_id"])
                return (
                    state
                    if state["status"] == "retry" and state["attempts"] == 1
                    else None
                )

            retry_state_after_failure = await wait_for(
                "feature outbox retry transition", retry_state_visible, timeout=30.0
            )
            partial_rolled_back = await feature_target_state(
                pool, tenant_a, owner_a1, retry_window_id
            )
            if any(partial_rolled_back.values()):
                raise HarnessFailure(
                    "partial feature-stage failure committed derived rows"
                )
            async with pool.acquire() as connection:
                await connection.execute(
                    "UPDATE pipeline_outbox SET available_at = NOW() WHERE id = $1",
                    retry_work["outbox_id"],
                )

            async def retry_complete() -> bool:
                state = await outbox_state(pool, retry_work["outbox_id"])
                return state["status"] == "completed"

            await wait_for(
                "feature outbox retry completion", retry_complete, timeout=60.0
            )
            retry_final_state = await outbox_state(pool, retry_work["outbox_id"])
            retry_derived_state = await feature_target_state(
                pool, tenant_a, owner_a1, retry_window_id
            )
            if retry_derived_state != {
                "feature_windows": 1,
                "anomaly_events": 1,
                "tracking_loops": 1,
            }:
                raise HarnessFailure(
                    "feature retry did not converge feature/anomaly/tracking rows exactly once"
                )
            partial_worker.kill()
            processes.remove(partial_worker)
            evidence["partial_feature_stage_and_outbox_retry"] = {
                "outbox_id": retry_work["outbox_id"],
                "window_id": retry_window_id,
                "state_after_injected_failure": retry_state_after_failure,
                "derived_rows_after_injected_failure": partial_rolled_back,
                "final_outbox_state": retry_final_state,
                "final_derived_rows": retry_derived_state,
            }

            # Feature-input replay: accept a future-dated event, wait until
            # its durable input is committed, then kill the service before its
            # window can close.  A fresh process must rebuild the in-memory
            # extractor from PostgreSQL and persist the deterministic window.
            replay_worker = start_worker(config, work_dir, "feature-replay")
            processes.append(replay_worker)
            await wait_for(
                "feature-replay worker heartbeat",
                lambda: worker_heartbeat(redis, "feature-replay"),
                timeout=60.0,
            )
            replay_start = datetime.now(timezone.utc)
            replay_time = datetime.now(timezone.utc) + timedelta(seconds=25)
            replay_event_ids = [f"E-FEATURE-REPLAY-{index}" for index in range(5)]
            replay_response = await post_ingest_batch(
                client,
                keys["valid_a1"]["raw"],
                replay_event_ids,
                timestamp=replay_time.isoformat(),
                service_name="remediation-06b-replay",
                message_prefix="remediation-06b replay",
            )
            if replay_response.status_code != 202:
                raise HarnessFailure("feature replay batch was not accepted")
            for replay_event_id in replay_event_ids:
                await wait_event_state(pool, tenant_a, owner_a1, replay_event_id)

            async def replay_input_ready() -> bool:
                async with pool.acquire() as connection:
                    count = await connection.fetchval(
                        """
                        SELECT count(*) FROM pipeline_feature_inputs
                        WHERE tenant_id = $1 AND owner_user_id = $2
                          AND event_id = ANY($3::text[])
                        """,
                        tenant_a,
                        owner_a1,
                        replay_event_ids,
                    )
                return int(count or 0) == len(replay_event_ids)

            await wait_for(
                "feature input before replay crash", replay_input_ready, timeout=60.0
            )
            pre_restart_windows = await feature_windows_for_event_time(
                pool, tenant_a, owner_a1, replay_time
            )
            if pre_restart_windows:
                raise HarnessFailure(
                    "feature window closed before the replay crash injection"
                )
            replay_worker.kill()
            processes.remove(replay_worker)

            replay_restart = start_worker(config, work_dir, "feature-replay-restart")
            processes.append(replay_restart)
            await wait_for(
                "feature-replay restart heartbeat",
                lambda: worker_heartbeat(redis, "feature-replay-restart"),
                timeout=60.0,
            )

            async def replay_window_ready() -> list[dict[str, Any]]:
                return await feature_windows_for_event_time(
                    pool, tenant_a, owner_a1, replay_time
                )

            # Five same-timestamp inputs satisfy the production threshold of
            # five logs per window. With the current 10-second window and
            # 5-second stride, two overlapping windows are expected; wait for
            # both before comparing restart convergence so a second restart
            # is not mistaken for recovery of an as-yet-unclosed overlap.
            replay_windows = await wait_for(
                "all feature windows recovered from durable input",
                lambda: _at_least_n(replay_window_ready, 2),
                timeout=75.0,
            )
            replay_states = {
                event_id: await event_state(pool, tenant_a, owner_a1, event_id)
                for event_id in replay_event_ids
            }
            if (
                any(state["feature_inputs"] != 1 for state in replay_states.values())
                or not replay_windows
            ):
                raise HarnessFailure(
                    "feature-input replay did not recover a durable window"
                )
            replay_restart.kill()
            processes.remove(replay_restart)
            replay_second = start_worker(config, work_dir, "feature-replay-second")
            processes.append(replay_second)
            await wait_for(
                "second feature-replay restart heartbeat",
                lambda: worker_heartbeat(redis, "feature-replay-second"),
                timeout=60.0,
            )
            await asyncio.sleep(15.0)
            replay_windows_after_second = await feature_windows_for_event_time(
                pool, tenant_a, owner_a1, replay_time
            )
            first_window_ids = {row["window_id"] for row in replay_windows}
            second_window_ids = {
                row["window_id"] for row in replay_windows_after_second
            }
            if (
                len(replay_windows_after_second) != len(second_window_ids)
                or second_window_ids != first_window_ids
            ):
                raise HarnessFailure(
                    "feature replay created duplicate deterministic windows on restart"
                )
            replay_second.kill()
            processes.remove(replay_second)
            evidence["feature_input_replay_restart"] = {
                "event_ids": replay_event_ids,
                "event_timestamp": replay_time.isoformat(),
                "replay_started_at": replay_start.isoformat(),
                "pre_restart_feature_windows": pre_restart_windows,
                "post_restart_feature_windows": replay_windows,
                "post_second_restart_feature_windows": replay_windows_after_second,
                "durable_event_states": replay_states,
                "same_window_ids_after_restart": second_window_ids == first_window_ids,
            }

            evidence["final"] = {
                "pending": await pending_snapshot(redis, config.stream_name),
                "group": await group_info(redis, config.stream_name),
                "ownerless_operational_rows": await ownerless_counts(pool),
            }
            if int(evidence["final"]["pending"]["summary"]["pending"]) != 0:
                raise HarnessFailure("final stream PEL is not empty")
        return evidence
    except Exception as exc:
        evidence["failure_type"] = type(exc).__name__
        evidence["failure"] = str(exc)
        evidence["process_log_tails"] = {
            process.log_path.name: _safe_log_tail(process.log_text(), config)
            for process in processes
        }
        debug_path = work_dir / "feature-replay-debug.jsonl"
        if debug_path.exists():
            evidence["feature_replay_debug"] = debug_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()[-120:]
        setattr(exc, "_remediation_06b_evidence", evidence)
        raise
    finally:
        for process in reversed(processes):
            process.terminate()
        await pool.close()
        await redis.aclose()


async def _pending_count(redis: Redis, stream_name: str) -> int:
    summary = await redis.xpending(stream_name, GROUP_NAME)
    return int(summary.get("pending", 0))


async def _pending_empty(redis: Redis, stream_name: str) -> bool:
    return await _pending_count(redis, stream_name) == 0


async def _at_least_n(
    predicate: Callable[[], Awaitable[list[Any]]], count: int
) -> list[Any] | None:
    value = await predicate()
    return value if len(value) >= count else None


async def _file_exists(path: Path) -> bool:
    return path.exists()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _safe_log_tail(log_text: str, config: HarnessConfig, limit: int = 120) -> list[str]:
    """Return bounded diagnostics with disposable credentials removed."""
    redacted = log_text
    for secret in (
        config.database_password,
        config.jwt_secret,
        config.fallback_key,
        config.database_url,
        config.redis_url,
    ):
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted.splitlines()[-limit:]


def write_evidence(config: HarnessConfig, evidence: dict[str, Any]) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / f"remediation-06b-{config.run_id}.json"
    path.write_text(
        json.dumps(_json_safe(evidence), indent=2, sort_keys=True), encoding="utf-8"
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-services",
        action="store_true",
        help="leave the uniquely named disposable Compose project running for inspection",
    )
    args = parser.parse_args()
    config = make_config()
    evidence: dict[str, Any] = {
        "status": "FAIL",
        "run_id": config.run_id,
        "compose_project": config.compose_project,
    }
    with tempfile.TemporaryDirectory(
        prefix=f"logsentinel-06b-{config.run_id}-"
    ) as raw_work_dir:
        work_dir = Path(raw_work_dir)
        try:
            start_disposable_services(config)
            run_migrations(config, work_dir)
            evidence = asyncio.run(run_scenario(config, work_dir))
            path = write_evidence(config, evidence)
            print(json.dumps({"status": "PASS", "evidence": str(path)}, sort_keys=True))
            return 0
        except Exception as exc:
            scenario_evidence = getattr(exc, "_remediation_06b_evidence", None)
            if isinstance(scenario_evidence, dict):
                evidence = scenario_evidence
            evidence["failure_type"] = type(exc).__name__
            evidence["failure"] = str(exc)
            path = write_evidence(config, evidence)
            print(json.dumps({"status": "FAIL", "evidence": str(path)}, sort_keys=True))
            return 1
        finally:
            if not args.keep_services:
                stop_disposable_services(config)


if __name__ == "__main__":
    raise SystemExit(main())
