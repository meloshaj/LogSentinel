import argparse
import asyncio
import random
import time
import uuid
from datetime import datetime, timezone
import aiohttp

API_URL = "http://localhost:8000/api/v1/ingest/bulk"
API_KEY = "ls_live_demo_key_123"

def generate_log_batch(batch_size):
    logs = []
    templates = [
        "User {} logged in successfully from {}",
        "Payment processed for order {} amount {}",
        "Failed to connect to database at {}:{}",
        "Cache miss for key {} in region {}",
        "API rate limit exceeded for tenant {}"
    ]
    for _ in range(batch_size):
        log = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service_name": random.choice(["auth-service", "payment-service", "web-frontend", "cache-layer"]),
            "level": random.choice(["INFO", "INFO", "WARN", "ERROR", "DEBUG"]),
            "message": random.choice(templates).format(random.randint(100, 999), random.randint(1000, 9999)),
            "trace_id": str(uuid.uuid4()),
            "span_id": str(uuid.uuid4())[:16],
            "metadata": {"region": "us-east-1"}
        }
        logs.append(log)
    return {"logs": logs}

async def worker(worker_id, session, queue, results):
    while True:
        batch_size = await queue.get()
        if batch_size is None:
            break
        
        payload = generate_log_batch(batch_size)
        headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
        
        start_time = time.perf_counter()
        try:
            async with session.post(API_URL, json=payload, headers=headers) as resp:
                status = resp.status
                await resp.read()
        except Exception:
            status = 500
        elapsed = time.perf_counter() - start_time
        
        results.append({
            "latency": elapsed,
            "status": status,
            "count": batch_size
        })
        queue.task_done()

async def main():
    parser = argparse.ArgumentParser(description="LogSentinel Ingestion Load Generator")
    parser.add_argument("--workers", type=int, default=10, help="Number of concurrent workers")
    parser.add_argument("--batch-size", type=int, default=200, help="Logs per batch")
    parser.add_argument("--rate", type=int, default=5000, help="Target logs per second")
    parser.add_argument("--duration", type=int, default=10, help="Test duration in seconds")
    args = parser.parse_args()

    target_batches_per_sec = args.rate / args.batch_size
    delay_between_batches = 1.0 / target_batches_per_sec if target_batches_per_sec > 0 else 0

    print(f"Starting load test: {args.rate} logs/sec, {args.batch_size} logs/batch, {args.workers} workers, {args.duration}s")
    
    queue = asyncio.Queue(maxsize=args.workers * 2)
    results = []

    async with aiohttp.ClientSession() as session:
        workers = [asyncio.create_task(worker(i, session, queue, results)) for i in range(args.workers)]
        
        start_time = time.time()
        batches_sent = 0
        
        while time.time() - start_time < args.duration:
            try:
                queue.put_nowait(args.batch_size)
                batches_sent += 1
                
                # Simple token bucket pacing
                expected_elapsed = batches_sent * delay_between_batches
                actual_elapsed = time.time() - start_time
                if expected_elapsed > actual_elapsed:
                    await asyncio.sleep(expected_elapsed - actual_elapsed)
            except asyncio.QueueFull:
                await asyncio.sleep(0.001)

        # Signal workers to exit
        for _ in range(args.workers):
            await queue.put(None)
            
        await asyncio.gather(*workers)
        
    actual_duration = time.time() - start_time
    total_logs = sum(r["count"] for r in results if r["status"] in [200, 202])
    
    if not results:
        print("No results!")
        return

    latencies = [r["latency"] for r in results]
    latencies.sort()
    
    print("\n--- Load Test Results ---")
    print(f"Duration:     {actual_duration:.2f}s")
    print(f"Total Logs:   {total_logs}")
    print(f"Throughput:   {total_logs / actual_duration:.2f} logs/sec")
    print(f"Avg Latency:  {sum(latencies)/len(latencies)*1000:.2f} ms")
    print(f"P50 Latency:  {latencies[int(len(latencies)*0.5)]*1000:.2f} ms")
    print(f"P95 Latency:  {latencies[int(len(latencies)*0.95)]*1000:.2f} ms")
    print(f"P99 Latency:  {latencies[int(len(latencies)*0.99)]*1000:.2f} ms")

    import json
    with open("load_test_results.json", "w") as f:
        json.dump({
            "duration": actual_duration,
            "total_logs": total_logs,
            "throughput": total_logs / actual_duration,
            "avg_latency": sum(latencies)/len(latencies)*1000,
            "p50_latency": latencies[int(len(latencies)*0.5)]*1000,
            "p95_latency": latencies[int(len(latencies)*0.95)]*1000,
            "p99_latency": latencies[int(len(latencies)*0.99)]*1000
        }, f)

if __name__ == "__main__":
    asyncio.run(main())
