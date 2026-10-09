"""Create and remotely verify a PostgreSQL physical base backup.

The command is intended for an isolated Compose backup profile.  It stages a
compressed PG16 tar backup only after checking free space, uploads every file
under a unique recovery-point prefix, verifies each object, then uploads a
manifest last.  It never modifies the PostgreSQL data directory.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    from .pitr_storage import (
        StorageConfigError,
        StorageOperationError,
        get_storage_config,
        list_objects,
        safe_key,
        sha256_file,
        upload_file_verified,
    )
except ImportError:  # pragma: no cover - direct container-script execution
    from pitr_storage import (
        StorageConfigError,
        StorageOperationError,
        get_storage_config,
        list_objects,
        safe_key,
        sha256_file,
        upload_file_verified,
    )


UTC = dt.timezone.utc
BASE_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
DEFAULT_MIN_FREE_BYTES = 1024 * 1024 * 1024
DEFAULT_RESERVE_BYTES = 256 * 1024 * 1024


class BaseBackupError(RuntimeError):
    """Raised when a physical base backup cannot be completed safely."""


def _setting(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _connection_env() -> tuple[str, str, str, str, str]:
    host = _setting("POSTGRES_HOST")
    user = _setting("POSTGRES_USER")
    database = _setting("POSTGRES_DB")
    if not host or not user or not database:
        raise BaseBackupError("PostgreSQL connection settings are incomplete")
    return (
        host,
        _setting("POSTGRES_PORT", "5432"),
        user,
        database,
        _setting("POSTGRES_PASSWORD"),
    )


def _run_readonly_query(query: str) -> str:
    host, port, user, database, password = _connection_env()
    child_env = os.environ.copy()
    child_env["PGPASSWORD"] = password
    command = [
        "psql",
        "-XAt",
        "--no-password",
        "--host",
        host,
        "--port",
        port,
        "--username",
        user,
        "--dbname",
        database,
        "--command",
        query,
    ]
    try:
        result = subprocess.run(
            command,
            env=child_env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BaseBackupError("PostgreSQL read-only preflight failed") from exc
    if result.returncode != 0:
        raise BaseBackupError("PostgreSQL read-only preflight failed")
    value = result.stdout.strip()
    if not value:
        raise BaseBackupError("PostgreSQL read-only preflight returned no value")
    return value


def _major(version: str) -> int:
    match = re.search(r"(?:^|\s)([0-9]+)(?:\.[0-9]+)", version)
    if not match:
        raise BaseBackupError("PostgreSQL major version could not be determined")
    return int(match.group(1))


def _free_space(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError as exc:
        raise BaseBackupError(
            "base-backup filesystem capacity could not be read"
        ) from exc


def _run_basebackup(
    *, staging: Path, host: str, port: str, user: str, password: str
) -> tuple[int, str]:
    child_env = os.environ.copy()
    child_env["PGPASSWORD"] = password
    command = [
        "pg_basebackup",
        "--no-password",
        "--host",
        host,
        "--port",
        port,
        "--username",
        user,
        "--pgdata",
        str(staging),
        "--format",
        "tar",
        "--gzip",
        "--wal-method",
        "stream",
        "--progress",
        "--manifest-checksums=SHA256",
    ]
    try:
        result = subprocess.run(
            command,
            env=child_env,
            capture_output=True,
            text=True,
            timeout=30 * 60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BaseBackupError("physical base-backup command failed") from exc
    if result.returncode != 0:
        raise BaseBackupError("physical base-backup command failed")
    return result.returncode, result.stdout[-200:]


def _recovery_files(staging: Path) -> list[Path]:
    paths = sorted(path for path in staging.iterdir() if path.is_file())
    if not paths:
        raise BaseBackupError("physical base-backup produced no files")
    for path in paths:
        if path.is_symlink() or not SAFE_FILE.fullmatch(path.name):
            raise BaseBackupError("physical base-backup produced an unsafe filename")
    return paths


def create_base_backup() -> dict[str, Any]:
    host, port, user, database, password = _connection_env()
    server_version = _run_readonly_query("SHOW server_version")
    server_major = _major(server_version)
    expected_major = int(_setting("PITR_REQUIRED_SERVER_MAJOR", "16"))
    if server_major != expected_major:
        raise BaseBackupError(
            "PostgreSQL server major version is outside the PG16 contract"
        )
    client_major = _major(
        subprocess.run(
            ["pg_basebackup", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout
    )
    if client_major != server_major:
        raise BaseBackupError("PostgreSQL base-backup client major version mismatch")

    database_size = int(
        _run_readonly_query("SELECT pg_database_size(current_database())")
    )
    staging_root = Path(_setting("BASE_BACKUP_DIR", "/pitr-base-backups"))
    if staging_root.resolve() == Path(staging_root.anchor).resolve():
        raise BaseBackupError("base-backup staging directory is unsafe")
    staging_root.mkdir(parents=True, exist_ok=True)
    free_bytes = _free_space(staging_root)
    minimum_free = int(_setting("PITR_MIN_FREE_BYTES", str(DEFAULT_MIN_FREE_BYTES)))
    required_free = max(minimum_free, database_size * 2 + DEFAULT_RESERVE_BYTES)
    if free_bytes < required_free:
        raise BaseBackupError("insufficient free space for a safe physical base backup")

    created_at = dt.datetime.now(UTC).replace(microsecond=0)
    base_id = created_at.strftime("%Y%m%dT%H%M%SZ") + f"-{secrets.token_hex(4)}"
    if not BASE_ID.fullmatch(base_id):
        raise BaseBackupError("generated physical base-backup identity is invalid")
    staging = Path(
        tempfile.mkdtemp(prefix=f".logsentinel-{base_id}-", dir=staging_root)
    )
    try:
        _run_basebackup(
            staging=staging,
            host=host,
            port=port,
            user=user,
            password=password,
        )
        files = _recovery_files(staging)
        config = get_storage_config("base")
        uploaded: list[dict[str, Any]] = []
        for path in files:
            key = safe_key(config, base_id, path.name)
            result = upload_file_verified(
                config,
                path,
                key,
                content_type="application/gzip"
                if path.name.endswith(".gz")
                else "application/octet-stream",
                metadata={"base_backup_id": base_id, "base_filename": path.name},
            )
            uploaded.append(
                {
                    "filename": path.name,
                    "key": key,
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                    "upload_status": result["status"],
                }
            )

        timescaledb = _run_readonly_query(
            "SELECT COALESCE((SELECT extversion FROM pg_extension WHERE extname='timescaledb'), 'not-installed')"
        )
        migration_head = _run_readonly_query(
            "SELECT COALESCE(MAX(version), 'NONE') FROM schema_migrations"
        )
        manifest = {
            "base_backup_id": base_id,
            "created_at_utc": created_at.isoformat().replace("+00:00", "Z"),
            "database_logical_identifier": database,
            "database_size_bytes": database_size,
            "server_version": server_version,
            "server_postgresql_major": server_major,
            "pg_basebackup_major": client_major,
            "timescaledb_version": timescaledb,
            "migration_head": migration_head,
            "format": "tar",
            "compression": "gzip",
            "wal_method": "stream",
            "files": uploaded,
            "base_backup_script_sha256": sha256_file(Path(__file__)),
        }
        manifest_path = staging / "recovery-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        manifest_key = safe_key(config, base_id, manifest_path.name)
        manifest_upload = upload_file_verified(
            config,
            manifest_path,
            manifest_key,
            content_type="application/json",
            metadata={"base_backup_id": base_id, "recovery_manifest": "true"},
        )
        expected_keys = {item["key"] for item in uploaded} | {manifest_key}
        observed_keys = {
            item["key"]
            for item in list_objects(config)
            if item["key"].startswith(config.prefix + base_id + "/")
        }
        if not expected_keys <= observed_keys:
            raise StorageOperationError("physical base-backup object set is incomplete")
        return {
            "status": "PASS",
            "base_backup_id": base_id,
            "created_at_utc": manifest["created_at_utc"],
            "database_size_bytes": database_size,
            "free_space_bytes_before": free_bytes,
            "required_free_space_bytes": required_free,
            "server_postgresql_major": server_major,
            "pg_basebackup_major": client_major,
            "timescaledb_version": timescaledb,
            "migration_head": migration_head,
            "object_count": len(expected_keys),
            "objects": uploaded
            + [
                {
                    "filename": manifest_path.name,
                    "key": manifest_key,
                    "size": manifest_path.stat().st_size,
                    "sha256": sha256_file(manifest_path),
                    "upload_status": manifest_upload["status"],
                }
            ],
        }
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    try:
        payload = create_base_backup()
    except (BaseBackupError, StorageConfigError, StorageOperationError) as exc:
        print(f"Physical base backup failed: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("Physical base backup failed: unexpected internal error", file=sys.stderr)
        return 1
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
