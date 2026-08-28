import asyncio
import asyncpg
import json
import time
import random
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Tuple, Any

# Connection Settings
DB_DSN = "postgres://logsentinel:logsentinel_secret@localhost:5432/logsentinel_db"

# Benchmark Settings
CONCURRENCY = 10
BATCH_SIZE = 1000
TARGET_LOGS = 100_000

# Telemetry
metrics = {
    "total_inserted": 0,
    "start_time": 0,
    "end_time": 0,
    "latencies": [],
    "failures": 0,
    "max_lock_duration_ms": 0,
    "drop_chunk_success": False
}

SERVICES = ["auth-service", "payment-api", "user-service"]
LEVELS = ["info", "warn", "error"]
METADATA_JSON = json.dumps({"test": True, "historical": False})
HIST_METADATA_JSON = json.dumps({"test": True, "historical": True})
PARAMS_JSON = json.dumps([])

def generate_batch(count: int, historical: bool = False) -> List[Tuple[Any, ...]]:
    batch = []
    now = datetime.now(timezone.utc)
    
    if historical:
        # Generate logs for 35 days ago to trigger retention policy drop
        base_time = now - timedelta(days=35)
        metadata = HIST_METADATA_JSON
    else:
        base_time = now
        metadata = METADATA_JSON

    for _ in range(count):
        log_id = uuid.uuid4().hex[:26]
        ts = base_time - timedelta(milliseconds=random.randint(0, 1000))
        service = random.choice(SERVICES)
        level = random.choice(LEVELS)
        raw_message = f"Simulated {level} message from {service}"
        template_id = "SIM_" + str(random.randint(1000, 9999))
        template_text = None
        
        batch.append((
            log_id, ts, service, raw_message, template_id, template_text, PARAMS_JSON,
            level, "stress-test", "benchmark", f"corr-{random.randint(1, 10000)}", metadata, ts, ts
        ))
    return batch

async def producer_task(pool: asyncpg.Pool, num_batches: int):
    for _ in range(num_batches):
        batch = generate_batch(BATCH_SIZE, historical=False)
        
        start_t = time.perf_counter()
        try:
            async with pool.acquire() as conn:
                await conn.copy_records_to_table(
                    "logs",
                    records=batch,
                    columns=[
                        "id", "timestamp", "service", "raw_message", "template_id", 
                        "template_text", "parameters", "level", "source", "environment", 
                        "correlation_id", "metadata", "parsed_at", "created_at"
                    ]
                )
            
            latency_ms = (time.perf_counter() - start_t) * 1000
            metrics["latencies"].append(latency_ms)
            metrics["total_inserted"] += len(batch)
        except Exception as e:
            print(f"Batch insert failed: {e}")
            metrics["failures"] += 1

async def background_retention_task(pool: asyncpg.Pool):
    # Wait a bit for ingestion to start spinning up
    await asyncio.sleep(2)
    print("\n[Retention Task] Generating historical chunk (35 days old)...")
    
    historical_batch = generate_batch(10, historical=True)
    try:
        async with pool.acquire() as conn:
            await conn.copy_records_to_table(
                "logs",
                records=historical_batch,
                columns=[
                    "id", "timestamp", "service", "raw_message", "template_id", 
                    "template_text", "parameters", "level", "source", "environment", 
                    "correlation_id", "metadata", "parsed_at", "created_at"
                ]
            )
        print("[Retention Task] Inserted historical logs. Chunk created.")
    except Exception as e:
        print(f"[Retention Task] Failed to insert historical logs: {e}")
        return

    # Trigger chunk drop
    print("[Retention Task] Triggering drop_chunks(INTERVAL '30 days')")
    start_t = time.perf_counter()
    try:
        async with pool.acquire() as conn:
            # Drop chunks older than 30 days
            await conn.execute("SELECT drop_chunks('logs', INTERVAL '30 days');")
        duration_ms = (time.perf_counter() - start_t) * 1000
        metrics["max_lock_duration_ms"] = duration_ms
        metrics["drop_chunk_success"] = True
        print(f"[Retention Task] Successfully dropped chunks in {duration_ms:.2f} ms without deadlocks.")
    except Exception as e:
        print(f"[Retention Task] Failed to drop chunks: {e}")

async def run_benchmark():
    print("Connecting to PostgreSQL/TimescaleDB...")
    pool = await asyncpg.create_pool(dsn=DB_DSN, min_size=CONCURRENCY+2, max_size=CONCURRENCY+5)
    
    print(f"Starting Ingest Benchmark: {TARGET_LOGS} logs total, Concurrency {CONCURRENCY}, Batch Size {BATCH_SIZE}")
    batches_per_worker = (TARGET_LOGS // BATCH_SIZE) // CONCURRENCY
    
    metrics["start_time"] = time.perf_counter()
    
    # Spawn ingestion producers
    producers = [asyncio.create_task(producer_task(pool, batches_per_worker)) for _ in range(CONCURRENCY)]
    
    # Spawn background retention task
    retention_worker = asyncio.create_task(background_retention_task(pool))
    
    # Wait for completion
    await asyncio.gather(*producers, retention_worker)
    
    metrics["end_time"] = time.perf_counter()
    
    await pool.close()
    print_report()

def print_report():
    total_time = metrics["end_time"] - metrics["start_time"]
    total_logs = metrics["total_inserted"]
    rate = total_logs / total_time if total_time > 0 else 0
    
    latencies = sorted(metrics["latencies"])
    p50 = latencies[int(len(latencies) * 0.50)] if latencies else 0
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
    p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0
    
    print("\n" + "="*50)
    print(" BENCHMARK RESULTS")
    print("="*50)
    print(f"Total Logs Inserted : {total_logs:,}")
    print(f"Total Time          : {total_time:.2f} seconds")
    print(f"Average Ingest Rate : {rate:,.2f} logs/sec")
    print(f"Total Failures      : {metrics['failures']}")
    print("-" * 50)
    print(" Batch Latency (ms):")
    print(f"   P50 : {p50:.2f} ms")
    print(f"   P95 : {p95:.2f} ms")
    print(f"   P99 : {p99:.2f} ms")
    print("-" * 50)
    print(f" Chunk Drop Success : {metrics['drop_chunk_success']}")
    print(f" Max Drop Lock Time : {metrics['max_lock_duration_ms']:.2f} ms")
    print("="*50)

if __name__ == "__main__":
    asyncio.run(run_benchmark())
