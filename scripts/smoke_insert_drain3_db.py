"""Smoke insert parsed Drain3 logs through LogRepository."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.core.database import DATABASE_URL, async_engine
from backend.app.repositories.db_health import fetch_latest_logs
from backend.app.repositories.log_repository import LogRepository


def parsed_log(index: int) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "raw_message": f"smoke auth-service login failed for user-{index} from 192.168.9.{index}",
        "template_id": f"smoke-template-{index}",
        "template_text": "smoke auth-service login failed for user<NUM> from <IP>",
        "parameters": [
            {"value": f"user-{index}", "mask_name": "NUM"},
            {"value": f"192.168.9.{index}", "mask_name": "IP"},
        ],
        "metadata": {
            "timestamp": now,
            "service": "auth-service",
            "level": "warning",
            "source": "smoke-script",
            "environment": "local",
            "correlation_id": f"smoke-correlation-{index}",
        },
        "parsed_at": now,
    }


async def main() -> int:
    print(f"Using DATABASE_URL: {os.getenv('DATABASE_URL', DATABASE_URL)}")

    repository = LogRepository(async_engine)
    inserted_count = await repository.bulk_insert_parsed_logs([parsed_log(1), parsed_log(2), parsed_log(3)])
    print(f"Inserted rows: {inserted_count}")

    rows = await fetch_latest_logs(async_engine)
    print("\nLatest parsed logs:")
    for row in rows:
        print(json.dumps(row, default=str, indent=2, sort_keys=True))

    await async_engine.dispose()
    return 0 if inserted_count == 3 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
