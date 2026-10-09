"""Report bounded, secret-free remote WAL archive metadata."""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

try:
    from .pitr_storage import (
        StorageConfigError,
        StorageOperationError,
        get_storage_config,
        list_objects,
    )
except ImportError:  # pragma: no cover - direct container-script execution
    from pitr_storage import (
        StorageConfigError,
        StorageOperationError,
        get_storage_config,
        list_objects,
    )


WAL_FILENAME = re.compile(
    r"^(?:[0-9A-Fa-f]{24}|[0-9A-Fa-f]{8}\.history|[0-9A-Fa-f]{24}\.[0-9A-Fa-f]{8}\.backup)$"
)


def collect_status(*, limit: int = 20, client: Any | None = None) -> dict[str, Any]:
    config = get_storage_config("wal")
    objects = [
        item
        for item in list_objects(config, client=client)
        if WAL_FILENAME.fullmatch(item["key"].removeprefix(config.prefix))
        and item["size"] > 0
    ]
    objects.sort(key=lambda item: (item["last_modified"], item["key"]))
    recent = objects[-max(1, limit) :]
    return {
        "status": "PASS" if objects else "FAIL",
        "bucket_configured": True,
        "prefix": config.prefix,
        "wal_object_count": len(objects),
        "recent_objects": recent,
        "latest": recent[-1] if recent else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    try:
        payload = collect_status(limit=max(1, min(args.limit, 100)))
    except (StorageConfigError, StorageOperationError):
        payload = {"status": "FAIL", "reason": "remote WAL status unavailable"}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
