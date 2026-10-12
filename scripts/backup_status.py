"""Report the last locally retained successful backup without reading secrets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def inspect_backup(manifest_path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "manifest": manifest_path.name,
            "valid": False,
            "reason": "manifest unreadable",
        }
    if not isinstance(manifest, dict):
        return {
            "manifest": manifest_path.name,
            "valid": False,
            "reason": "manifest is not an object",
        }
    dump_name = manifest.get("dump_filename")
    if (
        not isinstance(dump_name, str)
        or not dump_name
        or Path(dump_name).name != dump_name
    ):
        return {
            "manifest": manifest_path.name,
            "valid": False,
            "reason": "dump filename missing or unsafe",
        }
    dump_path = manifest_path.with_name(dump_name)
    checksum_path = dump_path.with_name(dump_path.name + ".sha256")
    if not dump_name or not dump_path.is_file() or not checksum_path.is_file():
        return {
            "manifest": manifest_path.name,
            "valid": False,
            "reason": "missing artifact",
        }
    checksum = checksum_path.read_text(encoding="utf-8").split()[0]
    actual = digest(dump_path)
    valid = (
        len(checksum) == 64
        and checksum == actual
        and checksum == str(manifest.get("dump_sha256", ""))
        and manifest.get("backup_filename") == dump_name
    )
    return {
        "manifest": manifest_path.name,
        "dump_filename": dump_name,
        "created_at_utc": manifest.get("created_at_utc"),
        "migration_head": manifest.get("migration_head"),
        "server_postgresql_major": manifest.get("server_postgresql_major"),
        "pg_dump_major": manifest.get("pg_dump_major"),
        "valid": valid,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--directory",
        default="/home/ubuntu/logsentinel-database-backups",
        help="local backup directory; defaults to the production recovery directory",
    )
    args = parser.parse_args()
    directory = Path(args.directory)
    records = [inspect_backup(path) for path in directory.glob("*.dump.manifest.json")]
    valid = [record for record in records if record.get("valid")]
    valid.sort(key=lambda record: str(record.get("created_at_utc", "")))
    payload = {
        "backup_directory": str(directory),
        "observed_manifest_count": len(records),
        "valid_backup_count": len(valid),
        "last_successful_backup": valid[-1] if valid else None,
        "status": "PASS" if valid else "FAIL",
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
