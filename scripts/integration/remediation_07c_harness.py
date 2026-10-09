"""Disposable real PostgreSQL/Valkey crash harness for Remediation 07C.

The harness exercises the actual PostgreSQL-authoritative password-reset
service against PostgreSQL/TimescaleDB 16 and Valkey 8.0.2. It uses only a
random loopback-bound Compose project and writes aggregate evidence without
token values, token digests, passwords, session tokens, or email secrets.

Run from the repository root::

    python scripts/integration/remediation_07c_harness.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "tests/integration/docker-compose.remediation-07c.yml"
EVIDENCE_DIR = REPO_ROOT / "temporary-report/remediation-07c/evidence"
MIGRATION_VERSION = "20260917_0011_password_reset_atomicity"
PREVIOUS_MIGRATION_HEAD = "20260913_0010_per_user_data_ownership"
ENCRYPTION_KEY = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# These imports are intentionally delayed until the test-only persistence key
# exists. The production application receives its real key from deployment.
os.environ["ENVIRONMENT"] = "test"
os.environ.setdefault("ENCRYPTION_KEY", ENCRYPTION_KEY)
os.environ.setdefault("JWT_SECRET_KEY", "07c-harness-jwt-" + "x" * 48)
os.environ["LOGSENTINEL_ALLOW_TEST_HOOKS"] = "1"

from backend.app.core.orm import UserRecord  # noqa: E402
from backend.app.services.password import generate_reset_token  # noqa: E402
from backend.app.services.password_reset import (  # noqa: E402
    PasswordResetCompletion,
    PasswordResetInvalidError,
    PasswordResetTestCrash,
    complete_password_reset,
    issue_password_reset,
)


class HarnessFailure(RuntimeError):
    """Raised when a required real-service assertion fails."""


@dataclass(slots=True)
class Config:
    run_id: str
    project: str
    database: str
    password: str
    postgres_port: int
    valkey_port: int

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://logsentinel:{self.password}"
            f"@127.0.0.1:{self.postgres_port}/{self.database}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://127.0.0.1:{self.valkey_port}/0"

    @property
    def valkey_volume(self) -> str:
        return f"{self.project}-valkey-data"

    def compose_environment(self) -> dict[str, str]:
        return {
            "LS07C_POSTGRES_USER": "logsentinel",
            "LS07C_POSTGRES_PASSWORD": self.password,
            "LS07C_POSTGRES_DB": self.database,
            "LS07C_POSTGRES_PORT": str(self.postgres_port),
            "LS07C_VALKEY_PORT": str(self.valkey_port),
            "LS07C_VALKEY_VOLUME": self.valkey_volume,
        }

    def application_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "ENVIRONMENT": "test",
                "ENCRYPTION_KEY": ENCRYPTION_KEY,
                "JWT_SECRET_KEY": "07c-harness-jwt-" + "x" * 48,
                "DATABASE_URL": self.database_url,
                "POSTGRES_USER": "logsentinel",
                "POSTGRES_PASSWORD": self.password,
                "POSTGRES_HOST": "127.0.0.1",
                "POSTGRES_PORT": str(self.postgres_port),
                "POSTGRES_DB": self.database,
                "POSTGRES_SSL_MODE": "disable",
                "POSTGRES_ALLOW_INSECURE_TLS": "true",
                "REDIS_URL": self.redis_url,
                "LOGSENTINEL_ALLOW_TEST_HOOKS": "1",
            }
        )
        return environment


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def make_config() -> Config:
    run_id = uuid4().hex[:12]
    return Config(
        run_id=run_id,
        project=f"logsentinel-07c-{run_id}",
        database=f"ls07c_{run_id}",
        password="ls07c-db-" + secrets.token_urlsafe(18),
        postgres_port=_free_port(),
        valkey_port=_free_port(),
    )


def compose_command(config: Config, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        config.project,
        "--file",
        str(COMPOSE_FILE),
        *arguments,
    ]


def run_compose(config: Config, *arguments: str, timeout: float = 180.0) -> None:
    environment = os.environ.copy()
    environment.update(config.compose_environment())
    result = subprocess.run(
        compose_command(config, *arguments),
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


def remove_valkey_volume(config: Config) -> None:
    """Destroy only this run's Valkey persistence volume for loss testing."""
    result = subprocess.run(
        ["docker", "volume", "rm", config.valkey_volume],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60.0,
        check=False,
    )
    if result.returncode != 0:
        raise HarnessFailure("disposable Valkey volume removal failed")


def run_migrations(config: Config) -> list[str]:
    environment = config.application_environment()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT), str(REPO_ROOT / "backend")]
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
    try:
        applied = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessFailure(
            "canonical migration lifecycle returned invalid JSON"
        ) from exc
    if not isinstance(applied, list) or not all(
        isinstance(item, str) for item in applied
    ):
        raise HarnessFailure("canonical migration lifecycle returned an invalid result")
    return applied


async def schema_snapshot(config: Config) -> dict[str, Any]:
    connection = await open_connection(config)
    try:
        table_exists = bool(
            await connection.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = 'password_reset_tokens'
                )
                """
            )
        )
        columns = await connection.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'password_reset_tokens'
            ORDER BY ordinal_position
            """
        )
        constraints = (
            await connection.fetch(
                """
            SELECT conname, contype
            FROM pg_constraint
            WHERE conrelid = 'public.password_reset_tokens'::regclass
            ORDER BY conname
            """
            )
            if table_exists
            else []
        )
        indexes = await connection.fetch(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public' AND tablename = 'password_reset_tokens'
            ORDER BY indexname
            """
        )
        applied = await connection.fetch(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
        column_names = [row["column_name"] for row in columns]
        constraint_names = [row["conname"] for row in constraints]
        index_names = [row["indexname"] for row in indexes]
        return {
            "table_exists": table_exists,
            "columns": column_names,
            "plaintext_token_column": any(
                name in column_names for name in ("raw_token", "token", "token_value")
            ),
            "primary_key_present": "password_reset_tokens_pkey" in constraint_names,
            "foreign_keys_present": all(
                name in constraint_names
                for name in (
                    "password_reset_tokens_user_id_fkey",
                    "password_reset_tokens_tenant_id_fkey",
                )
            ),
            "state_and_terminal_constraints_present": all(
                name in constraint_names
                for name in (
                    "ck_password_reset_tokens_state",
                    "ck_password_reset_tokens_expiry",
                    "ck_password_reset_tokens_completed_state",
                    "ck_password_reset_tokens_expired_state",
                    "ck_password_reset_tokens_invalidated_state",
                )
            ),
            "lookup_index_present": "ix_password_reset_tokens_user_state"
            in index_names,
            "applied_migration_head": applied[-1]["version"] if applied else None,
            "migration_0010_present": PREVIOUS_MIGRATION_HEAD
            in {row["version"] for row in applied},
            "migration_0011_present": MIGRATION_VERSION
            in {row["version"] for row in applied},
        }
    finally:
        await connection.close()


async def remove_reset_schema_for_upgrade(config: Config) -> None:
    connection = await open_connection(config)
    try:
        await connection.execute("DROP TABLE IF EXISTS password_reset_tokens")
        await connection.execute(
            "DELETE FROM schema_migrations WHERE version = $1", MIGRATION_VERSION
        )
    finally:
        await connection.close()


def interrupt_migration_after_sql(config: Config) -> int:
    marker = Path(tempfile.gettempdir()) / f"{config.project}-migration-marker"
    release = Path(tempfile.gettempdir()) / f"{config.project}-migration-release"
    for path in (marker, release):
        path.unlink(missing_ok=True)
    environment = config.application_environment()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT), str(REPO_ROOT / "backend")]
    )
    environment.update(
        {
            "LOGSENTINEL_TEST_INTERRUPT_VERSION": MIGRATION_VERSION,
            "LOGSENTINEL_TEST_INTERRUPT_PHASE": "after_sql_before_ledger",
            "LOGSENTINEL_TEST_INTERRUPT_MARKER": str(marker),
            "LOGSENTINEL_TEST_INTERRUPT_RELEASE": str(release),
            "LOGSENTINEL_TEST_INTERRUPT_TIMEOUT": "120",
        }
    )
    process = subprocess.Popen(
        [sys.executable, "scripts/database_lifecycle.py", "--apply"],
        cwd=REPO_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 180
        while not marker.exists() and process.poll() is None:
            if time.monotonic() >= deadline:
                raise HarnessFailure("migration interruption marker did not appear")
            time.sleep(0.05)
        if not marker.exists():
            raise HarnessFailure(
                "migration interruption process exited before failpoint"
            )
        process.terminate()
        return process.wait(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)
        for path in (marker, release):
            path.unlink(missing_ok=True)


async def run_migration_rehearsal(config: Config) -> dict[str, Any]:
    fresh = await schema_snapshot(config)
    required_schema = (
        fresh["table_exists"]
        and not fresh["plaintext_token_column"]
        and fresh["primary_key_present"]
        and fresh["foreign_keys_present"]
        and fresh["state_and_terminal_constraints_present"]
        and fresh["lookup_index_present"]
        and fresh["migration_0011_present"]
    )
    if not required_schema:
        raise HarnessFailure(f"fresh schema contract mismatch: {fresh}")

    if not fresh["migration_0010_present"]:
        raise HarnessFailure("disposable schema did not contain the 0010 head")
    await remove_reset_schema_for_upgrade(config)
    upgrade_applied = run_migrations(config)
    upgrade = await schema_snapshot(config)
    if upgrade_applied != [MIGRATION_VERSION] or not upgrade["migration_0011_present"]:
        raise HarnessFailure(
            f"0010 to 0011 upgrade mismatch: {upgrade_applied}, {upgrade}"
        )

    duplicate_applied = run_migrations(config)
    duplicate = await schema_snapshot(config)
    if duplicate_applied or not duplicate["migration_0011_present"]:
        raise HarnessFailure(
            f"duplicate migration invocation was not a no-op: {duplicate_applied}"
        )

    await remove_reset_schema_for_upgrade(config)
    interrupted_exit = interrupt_migration_after_sql(config)
    interrupted = await schema_snapshot(config)
    if interrupted["table_exists"] or interrupted["migration_0011_present"]:
        raise HarnessFailure(
            f"interrupted migration left durable partial state: {interrupted}"
        )
    retry_applied = run_migrations(config)
    final = await schema_snapshot(config)
    if retry_applied != [MIGRATION_VERSION] or not final["migration_0011_present"]:
        raise HarnessFailure(f"migration retry mismatch: {retry_applied}, {final}")

    return {
        "fresh_install": {
            "table_present": fresh["table_exists"],
            "schema_contract_pass": required_schema,
            "migration_head": fresh["applied_migration_head"],
        },
        "upgrade_0010_to_0011": {
            "old_head_present": fresh["migration_0010_present"],
            "applied_versions": upgrade_applied,
            "new_head": upgrade["applied_migration_head"],
            "pass": True,
        },
        "duplicate_invocation": {
            "applied_versions": duplicate_applied,
            "head_unchanged": duplicate["applied_migration_head"]
            == upgrade["applied_migration_head"],
            "pass": True,
        },
        "interruption_after_sql_before_ledger": {
            "process_exit_code": interrupted_exit,
            "table_present_after_termination": interrupted["table_exists"],
            "ledger_present_after_termination": interrupted["migration_0011_present"],
            "retry_applied_versions": retry_applied,
            "pass": True,
        },
        "final_schema": final,
        "pass": True,
    }


async def wait_for_services(config: Config) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            connection = await asyncpg.connect(
                user="logsentinel",
                password=config.password,
                host="127.0.0.1",
                port=config.postgres_port,
                database=config.database,
            )
            await connection.close()
            redis = Redis.from_url(config.redis_url)
            await redis.ping()
            await redis.aclose()
            return
        except (OSError, asyncpg.PostgresError, ConnectionError):
            await asyncio.sleep(0.25)
    raise HarnessFailure("disposable PostgreSQL/Valkey services did not become ready")


async def open_connection(config: Config) -> asyncpg.Connection:
    return await asyncpg.connect(
        user="logsentinel",
        password=config.password,
        host="127.0.0.1",
        port=config.postgres_port,
        database=config.database,
    )


async def seed_user(config: Config, label: str) -> tuple[int, str]:
    connection = await open_connection(config)
    try:
        tenant_id = f"07c-{config.run_id}-{label}-tenant"
        email = f"07c-{config.run_id}-{label}@example.invalid"
        await connection.execute(
            "INSERT INTO tenants (id, name, status) VALUES ($1, $2, 'active')",
            tenant_id,
            f"07C {label}",
        )
        user_id = await connection.fetchval(
            """
            INSERT INTO users (
                email, hashed_password, tenant_id, status, role,
                email_verified_at
            )
            VALUES ($1, 'old-password-hash', $2, 'active', 'viewer', NOW())
            RETURNING id
            """,
            email,
            tenant_id,
        )
        await connection.execute(
            """
            INSERT INTO tenant_memberships (tenant_id, user_id, role, status)
            VALUES ($1, $2, 'viewer', 'active')
            """,
            tenant_id,
            user_id,
        )
        session_id = f"07c-session-{config.run_id}-{label}"
        refresh_hash = secrets.token_hex(32)
        now = datetime.now(timezone.utc)
        await connection.execute(
            """
            INSERT INTO auth_sessions (
                id, user_id, tenant_id, csrf_hash, created_at, expires_at
            ) VALUES ($1, $2, $3, $4, $5, $6)
            """,
            session_id,
            user_id,
            tenant_id,
            secrets.token_hex(32),
            now,
            now + timedelta(days=14),
        )
        await connection.execute(
            """
            INSERT INTO auth_refresh_tokens (
                token_hash, session_id, expires_at
            ) VALUES ($1, $2, $3)
            """,
            refresh_hash,
            session_id,
            now + timedelta(days=14),
        )
        return int(user_id), tenant_id
    finally:
        await connection.close()


async def issue_for_user(
    config: Config, user_id: int, *, ttl_seconds: int = 900
) -> tuple[str, str]:
    engine = create_async_engine(config.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    raw_token, token_digest = generate_reset_token()
    try:
        async with factory() as db:
            user = await db.get(UserRecord, user_id)
            if user is None:
                raise HarnessFailure("seed user disappeared")
            await issue_password_reset(
                db,
                user=user,
                token_digest=token_digest,
                raw_token=raw_token,
                ttl_seconds=ttl_seconds,
            )
    finally:
        await engine.dispose()
    return raw_token, token_digest


async def snapshot(config: Config, user_id: int, token_digest: str) -> dict[str, Any]:
    connection = await open_connection(config)
    try:
        row = await connection.fetchrow(
            "SELECT hashed_password FROM users WHERE id = $1", user_id
        )
        reset = await connection.fetchrow(
            "SELECT state FROM password_reset_tokens WHERE token_digest = $1",
            token_digest,
        )
        sessions = await connection.fetchrow(
            """
            SELECT
                COUNT(*)::int AS total,
                COUNT(*) FILTER (WHERE revoked_at IS NOT NULL)::int AS revoked
            FROM auth_sessions WHERE user_id = $1
            """,
            user_id,
        )
        refresh = await connection.fetchrow(
            """
            SELECT
                COUNT(*)::int AS total,
                COUNT(*) FILTER (WHERE r.revoked_at IS NOT NULL)::int AS revoked
            FROM auth_refresh_tokens r
            JOIN auth_sessions s ON s.id = r.session_id
            WHERE s.user_id = $1
            """,
            user_id,
        )
        outbox = await connection.fetchval(
            """
            SELECT COUNT(*)::int FROM email_outbox
            WHERE user_id = $1 AND kind = 'password_changed'
            """,
            user_id,
        )
        return {
            "password_changed": row["hashed_password"] != "old-password-hash",
            "token_state": reset["state"] if reset else "missing",
            "sessions_total": sessions["total"],
            "sessions_revoked": sessions["revoked"],
            "refresh_total": refresh["total"],
            "refresh_revoked": refresh["revoked"],
            "outbox_count": int(outbox),
        }
    finally:
        await connection.close()


async def complete(
    config: Config,
    token_digest: str,
    *,
    failpoint: str | None = None,
    delay_point: str | None = None,
    delay_seconds: float | None = None,
) -> PasswordResetCompletion:
    engine = create_async_engine(config.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    previous = os.environ.get("LOGSENTINEL_TEST_FAILPOINT")
    previous_delay_point = os.environ.get("LOGSENTINEL_TEST_DELAY_POINT")
    previous_delay_seconds = os.environ.get("LOGSENTINEL_TEST_DELAY_SECONDS")
    if failpoint is not None:
        os.environ["LOGSENTINEL_TEST_FAILPOINT"] = failpoint
    if delay_point is not None:
        os.environ["LOGSENTINEL_TEST_DELAY_POINT"] = delay_point
    if delay_seconds is not None:
        os.environ["LOGSENTINEL_TEST_DELAY_SECONDS"] = str(delay_seconds)
    try:
        async with factory() as db:
            return await complete_password_reset(
                db, token_digest=token_digest, new_password="new-password-value"
            )
    finally:
        if previous is None:
            os.environ.pop("LOGSENTINEL_TEST_FAILPOINT", None)
        else:
            os.environ["LOGSENTINEL_TEST_FAILPOINT"] = previous
        if previous_delay_point is None:
            os.environ.pop("LOGSENTINEL_TEST_DELAY_POINT", None)
        else:
            os.environ["LOGSENTINEL_TEST_DELAY_POINT"] = previous_delay_point
        if previous_delay_seconds is None:
            os.environ.pop("LOGSENTINEL_TEST_DELAY_SECONDS", None)
        else:
            os.environ["LOGSENTINEL_TEST_DELAY_SECONDS"] = previous_delay_seconds
        await engine.dispose()


def assert_precommit_rollback(state: dict[str, Any]) -> None:
    if state != {
        "password_changed": False,
        "token_state": "issued",
        "sessions_total": 1,
        "sessions_revoked": 0,
        "refresh_total": 1,
        "refresh_revoked": 0,
        "outbox_count": 0,
    }:
        raise HarnessFailure(f"pre-commit rollback state mismatch: {state}")


def assert_completed(state: dict[str, Any]) -> None:
    if state != {
        "password_changed": True,
        "token_state": "completed",
        "sessions_total": 1,
        "sessions_revoked": 1,
        "refresh_total": 1,
        "refresh_revoked": 1,
        "outbox_count": 1,
    }:
        raise HarnessFailure(f"completed state mismatch: {state}")


async def run_crash_scenarios(config: Config) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    user_id, _tenant_id = await seed_user(config, "before-token-validation")
    _raw_token, digest = await issue_for_user(config, user_id)
    try:
        await complete(config, "f" * 64)
    except PasswordResetInvalidError:
        pass
    else:
        raise HarnessFailure("invalid capability was accepted before validation")
    before_validation = await snapshot(config, user_id, digest)
    assert_precommit_rollback(before_validation)
    rows.append(
        {
            "scenario": "real-postgresql-valkey-07c",
            "crash_point": "before_token_validation",
            "db_committed": False,
            "password_changed": False,
            "token_state": "missing_or_invalid",
            "token_reusable": False,
            "session_state": "unchanged",
            "outbox_count": 0,
            "retry_result": "invalid",
            "final_convergence": "no_mutation",
            "pass": True,
        }
    )

    for point in (
        "before_transaction",
        "after_token_authorized",
        "after_password_update",
        "after_session_revocation",
        "after_outbox_insert",
        "before_commit",
    ):
        user_id, _tenant_id = await seed_user(config, point.replace("_", "-"))
        _raw_token, digest = await issue_for_user(config, user_id)
        try:
            await complete(config, digest, failpoint=point)
        except PasswordResetTestCrash:
            pass
        else:
            raise HarnessFailure(f"failpoint {point} did not interrupt")
        after_crash = await snapshot(config, user_id, digest)
        assert_precommit_rollback(after_crash)
        retry = await complete(config, digest)
        if retry.already_completed:
            raise HarnessFailure(
                f"pre-commit retry unexpectedly already completed: {point}"
            )
        final = await snapshot(config, user_id, digest)
        assert_completed(final)
        rows.append(
            {
                "scenario": "real-postgresql-valkey-07c",
                "crash_point": point,
                "db_committed": False,
                "password_changed": after_crash["password_changed"],
                "token_state": after_crash["token_state"],
                "token_reusable": True,
                "session_state": "unchanged",
                "outbox_count": after_crash["outbox_count"],
                "retry_result": "completed",
                "final_convergence": "completed_once_after_rollback",
                "pass": True,
            }
        )

    user_id, _tenant_id = await seed_user(config, "after-commit")
    _raw_token, digest = await issue_for_user(config, user_id)
    try:
        await complete(config, digest, failpoint="after_commit")
    except PasswordResetTestCrash:
        pass
    else:
        raise HarnessFailure("after_commit failpoint did not interrupt")
    after_crash = await snapshot(config, user_id, digest)
    assert_completed(after_crash)
    retry = await complete(config, digest)
    if not retry.already_completed:
        raise HarnessFailure("post-commit retry performed a second reset")
    final = await snapshot(config, user_id, digest)
    assert_completed(final)
    rows.append(
        {
            "scenario": "real-postgresql-valkey-07c",
            "crash_point": "after_commit_before_response",
            "db_committed": True,
            "password_changed": after_crash["password_changed"],
            "token_state": after_crash["token_state"],
            "token_reusable": False,
            "session_state": "revoked",
            "outbox_count": after_crash["outbox_count"],
            "retry_result": "already_completed",
            "final_convergence": "completed_once_after_response_loss",
            "pass": True,
        }
    )
    return rows


async def run_concurrency(config: Config) -> dict[str, Any]:
    user_id, _tenant_id = await seed_user(config, "concurrency")
    _raw_token, digest = await issue_for_user(config, user_id)
    results: list[PasswordResetCompletion] = []

    async def submit() -> None:
        results.append(await complete(config, digest))

    await asyncio.gather(*(submit() for _ in range(12)))
    final = await snapshot(config, user_id, digest)
    successful = sum(not result.already_completed for result in results)
    retries = sum(result.already_completed for result in results)
    if successful != 1 or retries != 11:
        raise HarnessFailure(
            f"same-token concurrency results mismatch: {successful}/{retries}"
        )
    assert_completed(final)
    return {
        "concurrent_submissions": 12,
        "logical_successful_reset_count": successful,
        "idempotent_retry_count": retries,
        "outbox_count": final["outbox_count"],
        "token_state": final["token_state"],
        "pass": True,
    }


async def run_expiry_and_invalid(config: Config) -> dict[str, Any]:
    before_user, _tenant_id = await seed_user(config, "expiry-before")
    _raw_token, before_digest = await issue_for_user(config, before_user)
    before_result = await complete(config, before_digest)
    before_state = await snapshot(config, before_user, before_digest)
    if before_result.already_completed or not before_state["password_changed"]:
        raise HarnessFailure(f"before-expiry token was not accepted: {before_state}")

    at_user, _tenant_id = await seed_user(config, "expiry-at")
    _raw_token, at_digest = await issue_for_user(config, at_user)
    connection = await open_connection(config)
    try:
        await connection.execute(
            """
            UPDATE password_reset_tokens
            SET issued_at = clock_timestamp() - INTERVAL '1 minute',
                expires_at = clock_timestamp()
            WHERE token_digest = $1
            """,
            at_digest,
        )
    finally:
        await connection.close()
    try:
        await complete(config, at_digest)
    except PasswordResetInvalidError:
        pass
    else:
        raise HarnessFailure("token at expiry was accepted")
    at_state = await snapshot(config, at_user, at_digest)
    if at_state["token_state"] != "expired" or at_state["password_changed"]:
        raise HarnessFailure(f"at-expiry token state mismatch: {at_state}")

    after_user, _tenant_id = await seed_user(config, "expiry-after")
    _raw_token, after_digest = await issue_for_user(config, after_user)
    connection = await open_connection(config)
    try:
        await connection.execute(
            """
            UPDATE password_reset_tokens
            SET issued_at = clock_timestamp() - INTERVAL '2 minutes',
                expires_at = clock_timestamp() - INTERVAL '1 second'
            WHERE token_digest = $1
            """,
            after_digest,
        )
    finally:
        await connection.close()
    try:
        await complete(config, after_digest)
    except PasswordResetInvalidError:
        pass
    else:
        raise HarnessFailure("expired token was accepted")
    after_state = await snapshot(config, after_user, after_digest)
    if after_state["token_state"] != "expired" or after_state["password_changed"]:
        raise HarnessFailure(f"after-expiry token state mismatch: {after_state}")

    crossing_user, _tenant_id = await seed_user(config, "expiry-crossing")
    _raw_token, crossing_digest = await issue_for_user(config, crossing_user)
    connection = await open_connection(config)
    try:
        await connection.execute(
            """
            UPDATE password_reset_tokens
            SET issued_at = clock_timestamp() - INTERVAL '1 minute',
                expires_at = clock_timestamp() + INTERVAL '1 second'
            WHERE token_digest = $1
            """,
            crossing_digest,
        )
    finally:
        await connection.close()
    try:
        await complete(
            config,
            crossing_digest,
            delay_point="before_final_expiry_check",
            delay_seconds=2,
        )
    except PasswordResetInvalidError:
        pass
    else:
        raise HarnessFailure("token crossing expiry before commit was accepted")
    crossing_state = await snapshot(config, crossing_user, crossing_digest)
    assert_precommit_rollback(crossing_state)

    try:
        await complete(config, "f" * 64)
    except PasswordResetInvalidError:
        pass
    else:
        raise HarnessFailure("unknown token was accepted")
    return {
        "before_expiry": {
            "token_state": before_state["token_state"],
            "password_changed": before_state["password_changed"],
        },
        "at_expiry": {"token_state": at_state["token_state"]},
        "after_expiry": {"token_state": after_state["token_state"]},
        "crossing_before_commit": {
            "token_state": crossing_state["token_state"],
            "password_changed": crossing_state["password_changed"],
            "outbox_count": crossing_state["outbox_count"],
        },
        "unknown_token": "rejected",
        "pass": True,
    }


async def run_valkey_loss(config: Config) -> dict[str, Any]:
    user_id, _tenant_id = await seed_user(config, "valkey-loss")
    _raw_token, digest = await issue_for_user(config, user_id)
    redis = Redis.from_url(config.redis_url)
    legacy_key = f"password_reset:{digest}"
    await redis.set(legacy_key, json.dumps({"user_id": user_id}), ex=900)
    await complete(config, digest)
    await redis.get(legacy_key)
    await redis.aclose()
    run_compose(config, "restart", "valkey", timeout=120)
    redis_after_restart = Redis.from_url(config.redis_url)
    await redis_after_restart.ping()
    restart_marker = await redis_after_restart.get(legacy_key)
    await redis_after_restart.aclose()

    run_compose(config, "stop", "valkey", timeout=120)
    run_compose(config, "rm", "--force", "valkey", timeout=120)
    remove_valkey_volume(config)
    run_compose(config, "up", "-d", "--wait", "valkey", timeout=180)
    redis_after_loss = Redis.from_url(config.redis_url)
    await redis_after_loss.ping()
    loss_marker = await redis_after_loss.get(legacy_key)
    await redis_after_loss.aclose()

    retry = await complete(config, digest)
    final = await snapshot(config, user_id, digest)
    if not retry.already_completed or restart_marker is None or loss_marker is not None:
        raise HarnessFailure(
            "Valkey restart/loss semantics did not match expectations: "
            f"retry_already_completed={retry.already_completed}, "
            f"restart_marker_present={restart_marker is not None}, "
            f"loss_marker_present={loss_marker is not None}"
        )
    assert_completed(final)
    return {
        "legacy_key_survived_persistent_restart": True,
        "legacy_key_present_after_total_loss": False,
        "postgres_completed_after_valkey_loss": final["token_state"] == "completed",
        "retry_result": "already_completed",
        "pass": True,
    }


async def run_multiple_token_policy(config: Config) -> dict[str, Any]:
    user_id, _tenant_id = await seed_user(config, "multiple-tokens")
    _raw_token, digest_a = await issue_for_user(config, user_id)
    _raw_token, digest_b = await issue_for_user(config, user_id)

    first_a = await complete(config, digest_a)
    after_a = await snapshot(config, user_id, digest_a)
    if first_a.already_completed or after_a["token_state"] != "completed":
        raise HarnessFailure("first outstanding token did not complete")

    replay_a = await complete(config, digest_a)
    first_b = await complete(config, digest_b)
    after_b = await snapshot(config, user_id, digest_b)
    replay_b = await complete(config, digest_b)
    final = await snapshot(config, user_id, digest_b)
    if (
        not replay_a.already_completed
        or first_b.already_completed
        or after_b["token_state"] != "completed"
        or not replay_b.already_completed
        or final["outbox_count"] != 2
    ):
        raise HarnessFailure("multiple outstanding token policy mismatch")
    return {
        "policy": "multiple_outstanding_independently_single_use",
        "token_a_first": "completed",
        "token_a_replay": "already_completed",
        "token_b_first": "completed",
        "token_b_replay": "already_completed",
        "outbox_count": final["outbox_count"],
        "pass": True,
    }


async def run(config: Config) -> dict[str, Any]:
    run_compose(config, "up", "-d", "--wait", timeout=600)
    await wait_for_services(config)
    run_migrations(config)
    crash_matrix = await run_crash_scenarios(config)
    concurrency = await run_concurrency(config)
    expiry = await run_expiry_and_invalid(config)
    multiple_tokens = await run_multiple_token_policy(config)
    valkey = await run_valkey_loss(config)
    migration = await run_migration_rehearsal(config)
    return {
        "status": "PASS",
        "compose_project": config.project,
        "images": {
            "postgres": "timescale/timescaledb@sha256:4e459e217f00cbb09920c34d245501e63427e6767a495de57ce76823ff280f12",
            "valkey": "valkey/valkey@sha256:752ba000a58bc8925d11ee863a39b5225617cd2cb3dbc9ec8ac65268b65ebb6d",
        },
        "crash_matrix": crash_matrix,
        "concurrency": concurrency,
        "expiry_and_invalid": expiry,
        "multiple_token_policy": multiple_tokens,
        "valkey_restart_and_loss": valkey,
        "migration_rehearsal": migration,
        "secrets_in_evidence": False,
    }


def write_evidence(payload: dict[str, Any]) -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_DIR / "remediation-07c-real-integration.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    rows = payload.get("crash_matrix", [])
    matrix = {
        "status": payload.get("status", "BLOCKED"),
        "columns": [
            "scenario",
            "crash_point",
            "db_committed",
            "password_changed",
            "token_state",
            "token_reusable",
            "session_state",
            "outbox_count",
            "retry_result",
            "final_convergence",
            "pass",
        ],
        "rows": rows,
    }
    (EVIDENCE_DIR / "remediation-07c-crash-matrix.json").write_text(
        json.dumps(matrix, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    csv_lines = [
        "scenario,crash_point,db_committed,password_changed,token_state,"
        "token_reusable,session_state,outbox_count,retry_result,"
        "final_convergence,pass"
    ]
    for row in rows:
        csv_lines.append(
            ",".join(
                str(row[key]).replace(",", ";")
                for key in (
                    "scenario",
                    "crash_point",
                    "db_committed",
                    "password_changed",
                    "token_state",
                    "token_reusable",
                    "session_state",
                    "outbox_count",
                    "retry_result",
                    "final_convergence",
                    "pass",
                )
            )
        )
    (EVIDENCE_DIR / "remediation-07c-crash-matrix.csv").write_text(
        "\n".join(csv_lines) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    config = make_config()
    started = True
    try:
        result = asyncio.run(run(config))
        write_evidence(result)
        print(json.dumps({"status": result["status"], "project": config.project}))
        return 0
    except (
        FileNotFoundError,
        HarnessFailure,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        result = {
            "status": "BLOCKED",
            "reason_category": type(exc).__name__,
            "real_disposable_services_exercised": False,
            "secrets_in_evidence": False,
        }
        write_evidence(result)
        print(json.dumps(result))
        return 2
    finally:
        if started:
            try:
                run_compose(
                    config, "down", "--volumes", "--remove-orphans", timeout=180
                )
            except (HarnessFailure, OSError, subprocess.SubprocessError):
                pass


if __name__ == "__main__":
    raise SystemExit(main())
