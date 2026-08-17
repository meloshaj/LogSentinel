import argparse
import asyncio
import logging
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.sql import text

from ..core import get_database_settings

logger = logging.getLogger("logsentinel.cli.cleanup_storage")

async def cleanup_old_records(engine, days: int):
    """Delete records older than 'days' from non-hypertable tables."""
    logger.info("Cleaning up standard tables older than %d days...", days)
    
    query_anomaly = text(f"""
        DELETE FROM anomaly_events 
        WHERE created_at < NOW() - INTERVAL '{days} days'
    """)
    
    query_tracking = text(f"""
        DELETE FROM tracking_loops 
        WHERE created_at < NOW() - INTERVAL '{days} days'
    """)
    
    async with engine.begin() as conn:
        result_anomaly = await conn.execute(query_anomaly)
        logger.info("Deleted %d rows from anomaly_events", result_anomaly.rowcount)
        
        result_tracking = await conn.execute(query_tracking)
        logger.info("Deleted %d rows from tracking_loops", result_tracking.rowcount)

async def vacuum_analyze(engine):
    """Run VACUUM ANALYZE across all tables to reclaim storage."""
    logger.info("Running VACUUM (ANALYZE) on the database...")
    # VACUUM cannot run inside a transaction block, so we use execution_options(isolation_level="AUTOCOMMIT")
    async with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        await conn.execute(text("VACUUM (ANALYZE);"))
    logger.info("Database vacuumed and analyzed successfully.")

async def main():
    parser = argparse.ArgumentParser(description="LogSentinel Storage Maintenance CLI")
    parser.add_argument("--days", type=int, default=30, help="Retention period for standard tables in days")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    
    db_settings = get_database_settings()
    # asyncpg requires the +asyncpg driver
    db_url = db_settings.url
    
    engine = create_async_engine(db_url, echo=False)
    
    try:
        await cleanup_old_records(engine, args.days)
        await vacuum_analyze(engine)
    except Exception as e:
        logger.error("Failed to run storage cleanup: %s", str(e))
    finally:
        await engine.dispose()
        
if __name__ == "__main__":
    asyncio.run(main())
