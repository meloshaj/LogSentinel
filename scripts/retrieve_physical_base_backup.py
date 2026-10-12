"""Retrieve and verify one remote physical base-backup recovery point."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from .pitr_storage import (
        StorageConfigError,
        StorageOperationError,
        download_file_verified,
        get_storage_config,
        safe_key,
    )
except ImportError:  # pragma: no cover - direct container-script execution
    from pitr_storage import (
        StorageConfigError,
        StorageOperationError,
        download_file_verified,
        get_storage_config,
        safe_key,
    )


BASE_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def retrieve(base_id: str, destination: Path) -> dict[str, Any]:
    if not BASE_ID.fullmatch(base_id):
        raise StorageConfigError("physical base-backup identity is invalid")
    config = get_storage_config("base")
    destination.mkdir(parents=True, exist_ok=True)
    manifest_name = "recovery-manifest.json"
    manifest_key = safe_key(config, base_id, manifest_name)
    manifest_path = destination / manifest_name
    download_file_verified(config, manifest_key, manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageOperationError(
            "physical base-backup manifest is unreadable"
        ) from exc
    if not isinstance(manifest, dict) or manifest.get("base_backup_id") != base_id:
        raise StorageOperationError("physical base-backup manifest identity mismatch")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise StorageOperationError("physical base-backup manifest has no files")
    downloaded: list[dict[str, Any]] = []
    for record in files:
        if not isinstance(record, dict):
            raise StorageOperationError(
                "physical base-backup manifest file entry is invalid"
            )
        filename = str(record.get("filename", ""))
        if not SAFE_FILE.fullmatch(filename):
            raise StorageOperationError(
                "physical base-backup manifest filename is unsafe"
            )
        key = safe_key(config, base_id, filename)
        if record.get("key") != key or not HEX_DIGEST.fullmatch(
            str(record.get("sha256", ""))
        ):
            raise StorageOperationError(
                "physical base-backup manifest integrity metadata is invalid"
            )
        path = destination / filename
        result = download_file_verified(config, key, path)
        if (
            result["size"] != int(record["size"])
            or result["sha256"] != record["sha256"]
        ):
            raise StorageOperationError(
                "downloaded physical base-backup file does not match manifest"
            )
        downloaded.append({"filename": filename, **result})
    return {
        "status": "PASS",
        "base_backup_id": base_id,
        "destination": str(destination),
        "manifest": manifest_name,
        "file_count": len(downloaded),
        "files": downloaded,
        "migration_head": manifest.get("migration_head"),
        "server_postgresql_major": manifest.get("server_postgresql_major"),
        "pg_basebackup_major": manifest.get("pg_basebackup_major"),
        "timescaledb_version": manifest.get("timescaledb_version"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_id")
    parser.add_argument("destination", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = retrieve(args.base_id, args.destination)
    except (StorageConfigError, StorageOperationError):
        print(
            "Physical base-backup retrieval failed: object set unavailable or unverifiable",
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            "Physical base-backup retrieval failed: unexpected internal error",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
