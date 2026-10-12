"""Archive one PostgreSQL WAL segment to verified remote object storage."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

try:
    from .pitr_storage import (
        StorageConfigError,
        StorageOperationError,
        get_storage_config,
        safe_key,
        upload_file_verified,
    )
except ImportError:  # pragma: no cover - direct container-script execution
    from pitr_storage import (
        StorageConfigError,
        StorageOperationError,
        get_storage_config,
        safe_key,
        upload_file_verified,
    )


WAL_FILENAME = re.compile(
    r"^(?:[0-9A-Fa-f]{24}|[0-9A-Fa-f]{8}\.history|[0-9A-Fa-f]{24}\.[0-9A-Fa-f]{8}\.backup)$"
)


def validate_wal_source(source: Path, filename: str) -> None:
    """Validate PostgreSQL archive placeholders without interpreting them."""

    if not WAL_FILENAME.fullmatch(filename):
        raise StorageConfigError(
            "WAL filename is not a supported PostgreSQL archive name"
        )
    if source.name != filename:
        raise StorageConfigError("WAL source and archive filename do not match")
    if not source.is_file() or not source.stat().st_size:
        raise StorageOperationError("WAL source file is missing or empty")


def archive_wal(
    source: Path, filename: str, *, client: Any | None = None
) -> dict[str, Any]:
    validate_wal_source(source, filename)
    config = get_storage_config("wal")
    key = safe_key(config, filename)
    return upload_file_verified(
        config,
        source,
        key,
        content_type="application/octet-stream",
        metadata={"wal_filename": filename},
        client=client,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="PostgreSQL %%p archive source path")
    parser.add_argument("filename", help="PostgreSQL %%f archive filename")
    args = parser.parse_args(argv)
    try:
        result = archive_wal(args.source, args.filename)
    except (StorageConfigError, StorageOperationError) as exc:
        print(f"WAL archive failed: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("WAL archive failed: unexpected internal error", file=sys.stderr)
        return 1
    print(
        "WAL archive verified: "
        f"filename={args.filename} status={result['status']} "
        f"size={result['size']} sha256={result['sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
