"""Regression coverage for the Audit 3 database remediation.

The default suite is safe and does not require a running database.  The live
bootstrap test is opt-in and must be pointed at a disposable TimescaleDB
database with ``LOGSENTINEL_ALLOW_DISPOSABLE_SCHEMA_TEST=1``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import asyncpg
import pytest
from backend.app.core.orm import Base
from backend.app.core.settings import DatabaseSettings, get_database_settings
from scripts.database_lifecycle import (
    INIT_PATH,
    active_migration_files,
    apply_migrations,
    load_manifest,
    validate_schema,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
INIT_SQL = INIT_PATH.read_text(encoding="utf-8")


def test_enums_are_repeatable_and_instance_tuning_is_not_in_schema_bootstrap() -> None:
    """Enum creation is guarded and init.sql no longer changes instance config."""
    assert INIT_SQL.count("CREATE TYPE severity_level AS ENUM") == 1
    assert INIT_SQL.count("CREATE TYPE incident_status AS ENUM") == 1
    assert re.search(
        r"typname = 'severity_level'.*?CREATE TYPE severity_level AS ENUM",
        INIT_SQL,
        flags=re.DOTALL,
    )
    assert re.search(
        r"typname = 'incident_status'.*?CREATE TYPE incident_status AS ENUM",
        INIT_SQL,
        flags=re.DOTALL,
    )
    assert "ALTER SYSTEM SET" not in INIT_SQL
    assert "pg_reload_conf" not in INIT_SQL


def test_canonical_bootstrap_covers_every_active_orm_table() -> None:
    """Clean bootstrap must not rely on runtime metadata.create_all."""
    sql_tables = set(
        re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", INIT_SQL, flags=re.IGNORECASE)
    )
    assert set(Base.metadata.tables) <= sql_tables
    assert {"accounts", "external_identities", "schema_migrations"} <= sql_tables


def test_canonical_timescale_contract_is_explicit() -> None:
    """The current schema keeps the composite Timescale-compatible logs key."""
    assert "PRIMARY KEY (created_at, id)" in INIT_SQL
    assert "add_compression_policy('logs', INTERVAL '7 days'" in INIT_SQL
    assert "add_retention_policy('logs', INTERVAL '30 days'" in INIT_SQL
    assert "add_retention_policy('feature_windows'" not in INIT_SQL
    assert "create_hypertable('feature_windows'" not in INIT_SQL
    assert "20260822_0001_schema_reconciliation" in INIT_SQL


def test_manifest_blocks_incompatible_historical_sequences() -> None:
    """The runner cannot discover or execute destructive/conflicting SQL."""
    manifest = load_manifest()
    active = [migration["path"] for migration in manifest["active_migrations"]]
    blocked = {
        migration["path"]: migration
        for migration in manifest["legacy_migrations"]
    }

    assert active == [
        "scripts/migrations/20260822_0001_schema_reconciliation.sql"
    ]
    assert blocked["scripts/migrations/20260805_add_ulid_to_logs.sql"]["status"] == "BLOCKED"
    assert blocked["scripts/migrations/retention_policies.sql"]["status"] == "BLOCKED"
    assert blocked["scripts/migrations/20260806_safe_drop_procedure.sql"]["status"] == "BLOCKED"
    assert all(path not in active for path in blocked)
    assert [
        path.relative_to(REPO_ROOT).as_posix()
        for _, path in active_migration_files(manifest)
    ] == active


def test_migration_file_is_additive_and_fails_closed_for_legacy_logs() -> None:
    migration_path = REPO_ROOT / "scripts" / "migrations" / "20260822_0001_schema_reconciliation.sql"
    migration = migration_path.read_text(encoding="utf-8")

    assert "DROP TABLE" not in migration.upper()
    assert "DROP COLUMN" not in migration.upper()
    assert "DROP CONSTRAINT" not in migration.upper()
    assert "expected canonical created_at,id" in migration
    assert "logs is not a TimescaleDB hypertable" in migration


def test_database_settings_expose_one_asyncpg_ssl_contract() -> None:
    settings = DatabaseSettings(
        user="u",
        password="p",
        host="db",
        port=5433,
        db_name="demo",
        ssl_mode="disable",
    )
    kwargs = settings.asyncpg_connect_kwargs()

    assert kwargs["user"] == "u"
    assert kwargs["host"] == "db"
    assert kwargs["port"] == 5433
    assert kwargs["database"] == "demo"
    assert kwargs["ssl"] is False
    assert kwargs["timeout"] == 5.0
    assert kwargs["command_timeout"] == 30.0


def test_database_url_override_is_normalized_for_asyncpg() -> None:
    settings = DatabaseSettings(
        database_url_override=(
            "postgresql+asyncpg://user:password@db.example/demo?ssl=require"
        )
    )
    kwargs = settings.asyncpg_connect_kwargs()

    assert kwargs["dsn"].startswith("postgresql://")
    assert "sslmode=require" in kwargs["dsn"]
    assert "+asyncpg" not in kwargs["dsn"]


def test_database_settings_keep_legacy_worker_aliases_compatible() -> None:
    env = {
        "DB_USER": "legacy_user",
        "DB_PASS": "legacy_password",
        "DB_HOST": "legacy-host",
        "DB_PORT": "6543",
        "DB_NAME": "legacy_db",
        "DB_SSL_MODE": "require",
    }
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("DB_USER", env["DB_USER"])
        monkeypatch.setenv("DB_PASS", env["DB_PASS"])
        monkeypatch.setenv("DB_HOST", env["DB_HOST"])
        monkeypatch.setenv("DB_PORT", env["DB_PORT"])
        monkeypatch.setenv("DB_NAME", env["DB_NAME"])
        monkeypatch.setenv("DB_SSL_MODE", env["DB_SSL_MODE"])
        for name in (
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
            "POSTGRES_HOST",
            "POSTGRES_PORT",
            "POSTGRES_DB",
            "POSTGRES_SSL_MODE",
            "DATABASE_URL",
        ):
            monkeypatch.delenv(name, raising=False)
        settings = get_database_settings()

    assert settings.user == "legacy_user"
    assert settings.password == "legacy_password"
    assert settings.host == "legacy-host"
    assert settings.port == 6543
    assert settings.db_name == "legacy_db"
    assert settings.ssl_mode == "require"


@pytest.mark.asyncio
async def test_disposable_timescale_bootstrap_is_repeatable() -> None:
    """Run the real init/lifecycle path only when explicitly opted in."""
    raw_url = os.getenv("LOGSENTINEL_DB_REMEDIATION_TEST_URL")
    if not raw_url or os.getenv("LOGSENTINEL_ALLOW_DISPOSABLE_SCHEMA_TEST") != "1":
        pytest.skip("set disposable TimescaleDB test URL and explicit opt-in")

    settings = DatabaseSettings(database_url_override=raw_url)
    connection = await asyncpg.connect(**settings.asyncpg_connect_kwargs())
    try:
        await connection.execute(INIT_SQL)
        await connection.execute(INIT_SQL)
        applied = await apply_migrations(connection)
        result = await validate_schema(connection)
    finally:
        await connection.close()

    assert applied in ([], ["20260822_0001_schema_reconciliation"])
    assert result["schema_id"] == "logsentinel-timescale-v1"
    assert "0000_canonical_init" in result["applied_migrations"]
