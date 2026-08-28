"""Standalone benchmarking script for Database Batch Profiling."""

import argparse
import asyncio
import sys
import time
import uuid
from datetime import datetime, timezone

from backend.app.core.database import dispose_engine, init_engine
from backend.app.core.profiler import db_profiler
from backend.app.core.settings import get_database_settings
from backend.app.models import ParsedLog
from backend.app.repositories.log_repository import LogRepository
from sqlalchemy import text


def generate_synthetic_payloads(count: int) -> list[ParsedLog]:
    """Generate synthetic ParsedLog payloads in memory."""
    return [
        ParsedLog(
            timestamp=datetime.now(timezone.utc),
            service="benchmark_service",
            level="info",
            raw_message=f"Test message {i}",
            template_id="BENCH_TPL_8841",
            template_text="Test message <*>",
            parameters=[{"param": str(i)}],
            cluster_size=1,
            change_type="none",
            source="benchmark",
            environment="dev",
            correlation_id=str(uuid.uuid4()),
            metadata={"test_index": i},
            parsed_at=datetime.now(timezone.utc),
        )
        for i in range(count)
    ]


async def preflight_check(engine) -> None:
    """Execute a simple query to verify the database connection is live."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def run_benchmark(batch_sizes: list[int], total_records: int) -> None:
    # Force profiling enabled
    settings = get_database_settings()
    settings.profiling_enabled = True

    engine = init_engine(settings)

    # Pre-flight liveness check
    print(
        f"Executing pre-flight database connection check to {settings.host}:{settings.port}..."
    )
    try:
        await asyncio.wait_for(preflight_check(engine), timeout=3.0)
    except Exception as e:
        print(
            f"\n[FATAL] Database pre-flight check failed: {type(e).__name__}: {e}\n"
            f"Please verify that the PostgreSQL Docker container is running and healthy,\n"
            f"port mappings are correct, and it is actively accepting connections.\n"
            f"Target: {settings.host}:{settings.port} (Timeout=3.0s)",
            file=sys.stderr,
        )
        sys.exit(1)

    print("Pre-flight check passed. Synthesizing log payloads...")

    log_repository = LogRepository()

    print(
        f"{'Batch Size':<15} | {'Total Duration (s)':<20} | {'Records/Sec':<15} | {'p95 Latency (ms)'}"
    )
    print("-" * 75)

    for batch_size in batch_sizes:
        db_profiler.reset()

        # Generate the total records we want to test
        logs = generate_synthetic_payloads(total_records)

        start_time = asyncio.get_event_loop().time()

        # Push through directly, bypassing any batch manager error-swallowing
        # so that any future database syntax exception is re-raised immediately.
        for i in range(0, len(logs), batch_size):
            batch = logs[i : i + batch_size]

            batch_start = time.perf_counter()
            await log_repository.bulk_insert_parsed_logs(batch)
            batch_duration_ms = (time.perf_counter() - batch_start) * 1000.0

            db_profiler.track_batch(len(batch), batch_duration_ms)

        end_time = asyncio.get_event_loop().time()
        total_duration_s = end_time - start_time

        summary = db_profiler.get_profiling_summary()

        records_per_sec = summary.get("batches", {}).get(
            "throughput_records_per_sec", 0.0
        )
        p95_latency = summary.get("batches", {}).get("p95_duration_ms", 0.0)

        print(
            f"{batch_size:<15} | {total_duration_s:<20.4f} | {records_per_sec:<15.2f} | {p95_latency:.2f}"
        )

    await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Profile database batch writes.")
    parser.add_argument(
        "--batch-sizes",
        type=int,
        nargs="+",
        default=[100, 500, 1000, 2500],
        help="List of batch sizes to test",
    )
    parser.add_argument(
        "--total-records",
        type=int,
        default=10000,
        help="Total number of records to insert per batch size test",
    )

    args = parser.parse_args()
    asyncio.run(run_benchmark(args.batch_sizes, args.total_records))
