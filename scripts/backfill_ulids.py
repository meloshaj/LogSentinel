import asyncio
import logging
from datetime import datetime, timezone
import ulid
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
import sys
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

async def run_backfill(database_url: str):
    logger.info(f"Connecting to database at {database_url}...")
    engine = create_async_engine(database_url, echo=False)
    
    try:
        async with engine.begin() as conn:
            # 1. Add temporary string column
            logger.info("Adding temporary column `id_ulid` to logs table...")
            await conn.execute(text("ALTER TABLE logs ADD COLUMN IF NOT EXISTS id_ulid VARCHAR(26);"))
            
            # 2. Fetch all legacy logs that don't have a valid ULID (if any exist)
            logger.info("Fetching legacy logs...")
            result = await conn.execute(text("SELECT id, timestamp FROM logs WHERE id_ulid IS NULL;"))
            rows = result.fetchall()
            logger.info(f"Found {len(rows)} legacy logs to backfill.")
            
            # 3. Generate ULIDs and update batch
            if rows:
                updates = []
                for row_id, ts in rows:
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    log_id = ulid.from_timestamp(ts).str
                    updates.append({"row_id": row_id, "ulid": log_id})
                
                logger.info("Executing batch update for ULIDs...")
                await conn.execute(
                    text("UPDATE logs SET id_ulid = :ulid WHERE id = :row_id"),
                    updates
                )
                logger.info("Batch update completed.")
            
            # 4. Migrate the schema (handled by the SQL migration file ideally, but we can do it here if needed)
            # We'll leave the drop/swap to the actual SQL migration file.
            logger.info("Backfill complete. Please run the SQL migration to swap `id_ulid` to `id`.")
            
    except Exception as e:
        logger.error(f"Backfill failed: {e}")
        sys.exit(1)
    finally:
        await engine.dispose()

if __name__ == "__main__":
    # Expect standard DB URL from env or use default
    import os
    default_db = "postgresql+asyncpg://postgres:postgres@localhost:5432/logsentinel"
    db_url = os.environ.get("DATABASE_URL", default_db)
    asyncio.run(run_backfill(db_url))
