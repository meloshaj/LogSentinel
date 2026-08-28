"""Verify Drain3 PostgreSQL schema and print latest parsed logs."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.core.database import DATABASE_URL, async_engine
from backend.app.repositories.db_health import fetch_latest_logs, check_database_health


async def main() -> int:
    print(f"Using DATABASE_URL: {os.getenv('DATABASE_URL', DATABASE_URL)}")

    health = await check_database_health(async_engine)
    print("\nDatabase health:")
    print(json.dumps(health, indent=2, sort_keys=True))

    if not health["connected"]:
        print("\nDatabase is unreachable.")
        return 1
    if not health["table_exists"]:
        print("\nRequired table is missing: logs")
        return 1
    if health["missing_columns"]:
        print("\nMissing required columns:")
        for column in health["missing_columns"]:
            print(f"- {column}")
        return 1

    rows = await fetch_latest_logs(async_engine)
    print("\nLatest parsed logs:")
    if not rows:
        print("(no rows found)")
    for row in rows:
        print(json.dumps(row, default=str, indent=2, sort_keys=True))

    await async_engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
