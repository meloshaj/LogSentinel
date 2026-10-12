"""Explicitly rotate encrypted OAuth tokens after setting ENCRYPTION_KEY_PREVIOUS."""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import select

from backend.app.core.database import dispose_engine, get_database_settings, get_session_factory, init_engine
from backend.app.core.orm import AccountRecord, rotate_encrypted_value


async def main() -> None:
    if not os.getenv("ENCRYPTION_KEY_PREVIOUS"):
        raise RuntimeError("Set ENCRYPTION_KEY_PREVIOUS before rotating encrypted values")
    init_engine(get_database_settings())
    try:
        factory = get_session_factory()
        async with factory() as session:
            rows = (await session.execute(select(AccountRecord))).scalars().all()
            for row in rows:
                if row.access_token:
                    row.access_token = rotate_encrypted_value(row.access_token)
                if row.refresh_token:
                    row.refresh_token = rotate_encrypted_value(row.refresh_token)
            await session.commit()
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
