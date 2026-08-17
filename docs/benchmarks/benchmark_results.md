# LogSentinel Benchmark Results

**Generated At**: 2026-08-17 23:01:08

## Ingestion & Pipeline E2E Latency

| Target Rate | Batch Size | Actual Throughput | HTTP P95 (ms) | E2E P50 (ms) | E2E P95 (ms) | E2E P99 (ms) |
|-------------|------------|-------------------|---------------|--------------|--------------|--------------|
| 2000 logs/s | 50 | 326 logs/s | 125.5 | 2937.3 | 2937.3 | 2937.3 |
| 5000 logs/s | 250 | 0 logs/s | 8.9 | - | - | - |
| 10000 logs/s | 1000 | 4527 logs/s | 439.5 | 15566.6 | 15566.6 | 15566.6 |


> **Note**: E2E latency measures the time from the log payload leaving the load generator to the exact moment the parsed log or anomaly detection event arrives at the browser client over WebSockets.
