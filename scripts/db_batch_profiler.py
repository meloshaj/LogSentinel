"""Database batch profiling script for PostgreSQL bulk insert performance."""

import asyncio
import logging
import sys
import time
import random
import uuid
import ulid
from datetime import datetime, timezone
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("db_batch_profiler")

# Add the project root to sys.path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from backend.app.core import get_database_settings, init_engine, dispose_engine, get_engine
from backend.app.repositories.log_repository import LogRepository
from backend.app.models import ParsedLog
from backend.app.services.benchmarking import BenchmarkingCollector
from backend.app.core.orm import Base

def generate_mock_logs(count: int) -> list[ParsedLog]:
    """Generate synthetic ParsedLog payloads."""
    logs = []
    for _ in range(count):
        logs.append(
            ParsedLog(
                id=str(ulid.new()),
                timestamp=datetime.now(timezone.utc),
                service=random.choice(["auth-service", "payment-service", "api-gateway"]),
                level=random.choice(["INFO", "WARNING", "ERROR"]),
                raw_message=f"Mock log message {uuid.uuid4()}",
                template_id=f"E{random.randint(100, 999)}",
                template_text="Mock log message <*>",
                parameters=[{"uuid": str(uuid.uuid4())}],
                metadata={"test_run": "db_batch_profiler"},
                parsed_at=datetime.now(timezone.utc),
                source="profiler",
                environment="test",
                correlation_id=str(uuid.uuid4()),
            )
        )
    return logs

async def run_profiler():
    logger.info("Initializing database engine...")
    db_settings = get_database_settings()
    init_engine(db_settings)
    
    # Ensure tables exist
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    log_repo = LogRepository()
    benchmarking_collector = BenchmarkingCollector()
    
    batch_sizes = [100, 500, 1000, 2500]
    
    try:
        for size in batch_sizes:
            logger.info(f"--- Profiling batch size: {size} ---")
            
            # Generate
            logs = generate_mock_logs(size)
            
            # Insert and measure
            start_time = time.perf_counter()
            await log_repo.bulk_insert_parsed_logs(logs)
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            
            logger.info(f"Inserted {size} records in {duration_ms:.2f} ms ({duration_ms/size:.2f} ms/record)")
            
            # Route metrics to BenchmarkingCollector
            benchmarking_collector.record_db_batch_duration(duration_ms)
            
            await asyncio.sleep(0.5) # Give DB a breather
            
        health = benchmarking_collector.get_health_metrics()
        logger.info(f"Final Benchmarking Health Metrics: {health}")
        
    finally:
        await dispose_engine()
        logger.info("Database engine disposed.")

if __name__ == "__main__":
    asyncio.run(run_profiler())
