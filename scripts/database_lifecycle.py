"""Small deterministic schema lifecycle for the TimescaleDB-backed demo.

The Docker entrypoint executes ``scripts/init.sql`` only for an empty data
directory.  This module provides the explicit forward step that follows that
bootstrap and records applied versions.  Historical SQL files are not
discovered or executed implicitly; only entries in ``migration_manifest.json``
are eligible.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import asyncpg

try:
    from backend.app.security.redaction import sanitize_error_text
except ModuleNotFoundError:  # The production image exposes the package as ``app``.
    from app.security.redaction import sanitize_error_text

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "scripts" / "migration_manifest.json"
INIT_PATH = REPO_ROOT / "scripts" / "init.sql"
_ADVISORY_LOCK_KEY = "logsentinel.schema_lifecycle"
_LOGGER = logging.getLogger("logsentinel.database_lifecycle")

_REQUIRED_TABLES = {
    "logs",
    "incidents",
    "feature_windows",
    "anomaly_events",
    "tracking_loops",
    "users",
    "accounts",
    "external_identities",
    "password_reset_tokens",
    "schema_migrations",
}

_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     VARCHAR(128) PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum    VARCHAR(64)  NULL,
    description TEXT         NOT NULL DEFAULT ''
)
"""


class SchemaLifecycleError(RuntimeError):
    """Raised when the database cannot be advanced safely."""


def _resolve_repo_path(relative_path: str) -> Path:
    """Resolve a manifest path while preventing traversal outside the repo."""
    path = (REPO_ROOT / relative_path).resolve()
    if not path.is_relative_to(REPO_ROOT):
        raise SchemaLifecycleError(f"manifest path escapes repository: {relative_path}")
    return path


def load_manifest() -> dict[str, Any]:
    """Load and validate the repository-owned migration manifest."""
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SchemaLifecycleError(f"cannot load {MANIFEST_PATH}: {exc}") from exc

    if not isinstance(manifest, dict):
        raise SchemaLifecycleError("migration manifest must contain a JSON object")

    bootstrap = manifest.get("canonical_bootstrap")
    if not isinstance(bootstrap, dict):
        raise SchemaLifecycleError("manifest is missing canonical_bootstrap")
    if bootstrap.get("path") != "scripts/init.sql":
        raise SchemaLifecycleError("canonical bootstrap must be scripts/init.sql")
    if not _resolve_repo_path(str(bootstrap["path"])).is_file():
        raise SchemaLifecycleError("canonical bootstrap file does not exist")

    migrations = manifest.get("active_migrations")
    if not isinstance(migrations, list) or not migrations:
        raise SchemaLifecycleError("manifest must define at least one active migration")

    versions: set[str] = set()
    previous_version = ""
    for migration in migrations:
        if not isinstance(migration, dict):
            raise SchemaLifecycleError("each active migration must be an object")
        version = migration.get("version")
        path_value = migration.get("path")
        if not isinstance(version, str) or not version:
            raise SchemaLifecycleError("each active migration needs a version")
        if version in versions:
            raise SchemaLifecycleError(f"duplicate active migration version: {version}")
        if version <= previous_version:
            raise SchemaLifecycleError(
                "active migrations must be in strict version order"
            )
        if not isinstance(path_value, str):
            raise SchemaLifecycleError(f"migration {version} has no path")
        if not _resolve_repo_path(path_value).is_file():
            raise SchemaLifecycleError(f"migration file does not exist: {path_value}")
        versions.add(version)
        previous_version = version

    return manifest


def active_migration_files(
    manifest: dict[str, Any] | None = None,
) -> list[tuple[dict[str, Any], Path]]:
    """Return only allowlisted forward migrations in manifest order."""
    loaded = manifest or load_manifest()
    return [
        (migration, _resolve_repo_path(migration["path"]))
        for migration in loaded["active_migrations"]
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _ensure_migration_table(connection: asyncpg.Connection) -> None:
    await connection.execute(_SCHEMA_MIGRATIONS_DDL)


def migration_sql(path: Path) -> str:
    """Keep the ledger and SQL in one driver-owned transaction.

    Immutable historical files contain outer BEGIN/COMMIT wrappers. Strip only
    those wrappers in memory; never rewrite the checksummed source files.
    """
    sql = path.read_text(encoding="utf-8")
    sql = re.sub(r"(?im)^BEGIN;\s*$", "", sql, count=1)
    sql = re.sub(r"(?im)^COMMIT;\s*$", "", sql)
    if not sql.strip():
        raise SchemaLifecycleError(f"migration {path.name} is empty")
    return sql


async def preflight_history(
    connection: asyncpg.Connection, loaded: dict[str, Any]
) -> None:
    """Check every historic checksum and frozen prerequisite before any DDL."""
    rows_by_version: dict[str, asyncpg.Record | None] = {}
    for migration, path in active_migration_files(loaded):
        row = await connection.fetchrow(
            "SELECT checksum FROM schema_migrations WHERE version = $1",
            migration["version"],
        )
        rows_by_version[migration["version"]] = row
        if (
            row is not None
            and row["checksum"] is not None
            and row["checksum"] != _sha256(path)
        ):
            raise SchemaLifecycleError(
                f"checksum mismatch for applied migration {migration['version']}"
            )
        if migration.get("execution") == "frozen-history-only" and row is None:
            raise SchemaLifecycleError(
                f"{migration['version']} is frozen: staged backfill and explicit validated cutover required"
            )

    applied_versions = {
        version for version, row in rows_by_version.items() if row is not None
    }
    for index, migration in enumerate(loaded["active_migrations"]):
        version = migration["version"]
        if rows_by_version[version] is None:
            later_applied = [
                later["version"]
                for later in loaded["active_migrations"][index + 1 :]
                if later["version"] in applied_versions
            ]
            if later_applied:
                raise SchemaLifecycleError(
                    "migration ledger gap: "
                    f"{version} is missing while later migrations are recorded "
                    f"({', '.join(later_applied[:3])})"
                )


async def _test_interrupt(version: str, phase: str) -> None:
    """Pause only under an explicit disposable-test hook.

    The hook lets the migration rehearsal terminate the exact lifecycle
    process before the ledger write.  It is deliberately opt-in and requires
    a second allow flag so production cannot accidentally honor a test
    environment variable copied from a fixture.
    """
    if os.getenv("LOGSENTINEL_ALLOW_TEST_HOOKS") != "1":
        return
    if os.getenv("LOGSENTINEL_TEST_INTERRUPT_VERSION") != version:
        return
    if os.getenv("LOGSENTINEL_TEST_INTERRUPT_PHASE") != phase:
        return

    marker_value = os.getenv("LOGSENTINEL_TEST_INTERRUPT_MARKER", "").strip()
    release_value = os.getenv("LOGSENTINEL_TEST_INTERRUPT_RELEASE", "").strip()
    if not marker_value or not release_value:
        raise SchemaLifecycleError(
            "test interruption hook requires marker and release paths"
        )
    marker = Path(marker_value)
    release = Path(release_value)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({"version": version, "phase": phase, "pid": os.getpid()}) + "\n",
        encoding="utf-8",
    )
    timeout = float(os.getenv("LOGSENTINEL_TEST_INTERRUPT_TIMEOUT", "120"))
    deadline = time.monotonic() + timeout
    while not release.exists():
        if time.monotonic() >= deadline:
            raise SchemaLifecycleError("test interruption hook timed out")
        await asyncio.sleep(0.05)


async def apply_migrations(
    connection: asyncpg.Connection,
    manifest: dict[str, Any] | None = None,
) -> list[str]:
    """Apply allowlisted migrations exactly once and return applied versions.

    The canonical bootstrap marker is required.  A database with an unknown
    or legacy schema therefore fails closed instead of receiving a guessed
    migration sequence.
    """
    loaded = manifest or load_manifest()
    await _ensure_migration_table(connection)

    bootstrap_version = loaded["canonical_bootstrap"]["version"]
    bootstrap_row = await connection.fetchrow(
        "SELECT version FROM schema_migrations WHERE version = $1",
        bootstrap_version,
    )
    if bootstrap_row is None:
        raise SchemaLifecycleError(
            "canonical bootstrap is not recorded; run init.sql on an empty "
            "database or explicitly adopt a validated existing schema"
        )

    applied: list[str] = []
    await connection.execute(
        "SELECT pg_advisory_lock(hashtext($1))", _ADVISORY_LOCK_KEY
    )
    try:
        await preflight_history(connection, loaded)
        for migration, path in active_migration_files(loaded):
            version = migration["version"]
            checksum = _sha256(path)
            row = await connection.fetchrow(
                "SELECT checksum FROM schema_migrations WHERE version = $1",
                version,
            )
            if row is not None:
                stored_checksum = row["checksum"]
                if stored_checksum not in (None, checksum):
                    raise SchemaLifecycleError(
                        f"checksum mismatch for applied migration {version}"
                    )
                continue

            sql = migration_sql(path)

            _LOGGER.info("Applying database migration %s", version)
            await _test_interrupt(version, "before_transaction")
            async with connection.transaction():
                await connection.execute("SET LOCAL lock_timeout = '5s'")
                await connection.execute("SET LOCAL statement_timeout = '5min'")
                await connection.execute(sql)
                await _test_interrupt(version, "after_sql_before_ledger")
                await connection.execute(
                    """
                    INSERT INTO schema_migrations (version, checksum, description)
                    VALUES ($1, $2, $3)
                    """,
                    version,
                    checksum,
                    migration.get("description", ""),
                )
            applied.append(version)
    finally:
        await connection.execute(
            "SELECT pg_advisory_unlock(hashtext($1))", _ADVISORY_LOCK_KEY
        )

    return applied


async def _known_application_tables(connection: asyncpg.Connection) -> set[str]:
    rows = await connection.fetch(
        """
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename = ANY($1::text[])
        """,
        list(_REQUIRED_TABLES - {"schema_migrations"}),
    )
    return {row["tablename"] for row in rows}


async def bootstrap_database(connection: asyncpg.Connection) -> list[str]:
    """Run the canonical bootstrap only when no application tables exist."""
    existing = await _known_application_tables(connection)
    if existing:
        raise SchemaLifecycleError(
            "refusing --bootstrap because application tables already exist: "
            + ", ".join(sorted(existing))
        )

    _LOGGER.info("Running canonical TimescaleDB bootstrap from %s", INIT_PATH)
    await connection.execute(INIT_PATH.read_text(encoding="utf-8"))
    return await apply_migrations(connection)


async def validate_schema(connection: asyncpg.Connection) -> dict[str, Any]:
    """Validate the canonical object inventory and Timescale logs contract."""
    manifest = load_manifest()
    await preflight_history(connection, manifest)
    rows = await connection.fetch(
        """
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
        """
    )
    tables = {row["tablename"] for row in rows}
    missing_tables = sorted(_REQUIRED_TABLES - tables)
    if missing_tables:
        raise SchemaLifecycleError(
            "canonical schema is missing tables: " + ", ".join(missing_tables)
        )

    extension = await connection.fetchval(
        "SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'"
    )
    if extension != 1:
        raise SchemaLifecycleError("timescaledb extension is not installed")

    hypertable = await connection.fetchrow(
        """
        SELECT h.hypertable_schema, h.hypertable_name, d.time_interval as chunk_time_interval
        FROM timescaledb_information.hypertables h
        JOIN timescaledb_information.dimensions d
          ON h.hypertable_schema = d.hypertable_schema
         AND h.hypertable_name = d.hypertable_name
        WHERE h.hypertable_schema = 'public' AND h.hypertable_name = 'logs'
        """
    )
    if hypertable is None:
        raise SchemaLifecycleError("public.logs is not a TimescaleDB hypertable")

    bootstrap_version = await connection.fetchval(
        """
        SELECT version
        FROM schema_migrations
        WHERE version = '0000_canonical_init'
        """
    )
    if bootstrap_version is None:
        raise SchemaLifecycleError("canonical bootstrap marker is missing")

    applied = await connection.fetch(
        "SELECT version FROM schema_migrations ORDER BY version"
    )
    applied_versions = {row["version"] for row in applied}
    missing_active = [
        migration["version"]
        for migration in manifest["active_migrations"]
        if migration["version"] not in applied_versions
    ]
    if missing_active:
        raise SchemaLifecycleError(
            "active migrations are not recorded: " + ", ".join(missing_active)
        )
    return {
        "schema_id": "logsentinel-timescale-v1",
        "tables": sorted(tables & _REQUIRED_TABLES),
        "logs_hypertable": True,
        "chunk_time_interval": str(hypertable["chunk_time_interval"]),
        "applied_migrations": [row["version"] for row in applied],
    }


async def _connect() -> asyncpg.Connection:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        from backend.app.core.settings import get_database_settings
    except ModuleNotFoundError as exc:
        # The repository layout imports as ``backend.app`` locally, while the
        # production image copies ``backend/`` into its /app workdir and
        # therefore imports the same package as ``app``.
        if exc.name not in {"backend", "backend.app", "backend.app.core"}:
            raise
        from app.core.settings import get_database_settings

    settings = get_database_settings()
    return await asyncpg.connect(**settings.asyncpg_connect_kwargs())


async def _run(mode: str) -> dict[str, Any] | list[str]:
    connection = await _connect()
    try:
        if mode == "bootstrap":
            result = await bootstrap_database(connection)
            await validate_schema(connection)
            return result
        if mode == "apply":
            result = await apply_migrations(connection)
            await validate_schema(connection)
            return result
        if mode == "ensure":
            existing = await _known_application_tables(connection)
            result = (
                await bootstrap_database(connection)
                if not existing
                else await apply_migrations(connection)
            )
            await validate_schema(connection)
            return result
        if mode == "validate":
            return await validate_schema(connection)
    finally:
        await connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--bootstrap",
        action="store_const",
        const="bootstrap",
        dest="mode",
        help="bootstrap an empty database, then apply active migrations",
    )
    mode.add_argument(
        "--apply",
        action="store_const",
        const="apply",
        dest="mode",
        help="apply allowlisted forward migrations after init.sql",
    )
    mode.add_argument(
        "--ensure",
        action="store_const",
        const="ensure",
        dest="mode",
        help="bootstrap an empty database or apply migrations to a known schema",
    )
    mode.add_argument(
        "--validate",
        action="store_const",
        const="validate",
        dest="mode",
        help="connect and validate the canonical schema and lifecycle ledger",
    )
    parser.set_defaults(mode="validate")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        result = asyncio.run(_run(args.mode))
    except (OSError, asyncpg.PostgresError, SchemaLifecycleError) as exc:
        _LOGGER.error("Database lifecycle failed: %s", sanitize_error_text(exc))
        return 1

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
