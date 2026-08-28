# Day 1: Production Load Testing & Capacity Planning Runbook

This runbook outlines the methodology, commands, parameter tuning formulas, and target SLOs for **Day 1: Soak Testing & Capacity Planning**.

---

## 🎯 Production Performance Targets (SLOs)

| Metric | Target / SLO | Failure Threshold | Description |
| :--- | :--- | :--- | :--- |
| **Ingestion Throughput** | `≥ 10,000 logs/sec` | `< 5,000 logs/sec` | Sustained throughput per worker node |
| **HTTP Latency (p95)** | `< 35 ms` | `> 100 ms` | Ingestion API response time |
| **HTTP Latency (p99)** | `< 120 ms` | `> 300 ms` | Ingestion API worst-case latency |
| **E2E Pipeline Latency** | `< 250 ms` | `> 1,000 ms` | Ingestion -> Drain -> ML -> Broadcast |
| **Valkey Stream Memory** | Bounded (≤ 500k entries) | Unbounded growth | Approximate stream trimming (`MAXLEN ~ 500000`) |
| **TimescaleDB Batch Time** | `< 50 ms / 500 logs` | `> 200 ms / 500 logs` | Database bulk insertion efficiency |

---

## 🛠️ Step 1: Database Batch Insertion Profiling

Before testing the end-to-end pipeline, profile the TimescaleDB hypertable insert performance across batch sizes.

### Run Profiler:
```bash
python scripts/db_batch_profiler.py
```

### Tuning Matrix:
* **Low Volume (< 1k logs/sec)**: `DRAIN3_BATCH_SIZE=100`, `DRAIN3_FLUSH_INTERVAL_SECONDS=1.0`
* **Medium Volume (1k - 10k logs/sec)**: `DRAIN3_BATCH_SIZE=500`, `DRAIN3_FLUSH_INTERVAL_SECONDS=2.0`
* **High Volume (10k - 50k+ logs/sec)**: `DRAIN3_BATCH_SIZE=1000`, `DRAIN3_FLUSH_INTERVAL_SECONDS=0.25`

---

## ⚡ Step 2: High-Concurrency End-to-End Soak Test

Run the high-concurrency benchmark script simulating production log streams:

```bash
# Run 10k records across concurrency levels [10, 50, 100]
python scripts/benchmark_performance.py --concurrency 10,50,100 --total-records 10000

# Sustained 4-hour soak test simulation (50,000 records, 100 workers)
python scripts/benchmark_performance.py --concurrency 100 --total-records 50000 --output benchmark_soak_results.json
```

### Key Metrics to Monitor in Prometheus during Soak:
1. `rate(logsentinel_ingest_requests_total[1m])`: Confirms sustained incoming rate.
2. `rate(logsentinel_batch_ingestion_size_total[1m])`: Confirms actual log throughput.
3. `logsentinel_active_websocket_connections`: Verifies WebSocket stability under heavy broadcast.

---

## ⚖️ Step 3: Kubernetes Resource Sizing & Concurrency Matrix

Based on our benchmarks, the following resource requests and limits are configured in `deploy/helm/logsentinel/values.yaml`:

```yaml
resources:
  api:
    requests:
      cpu: "250m"
      memory: "512Mi"
    limits:
      cpu: "1000m"
      memory: "1Gi"
  drainWorker:
    requests:
      cpu: "500m"
      memory: "1Gi"
    limits:
      cpu: "2000m"
      memory: "2Gi"
  eventWorker:
    requests:
      cpu: "250m"
      memory: "512Mi"
    limits:
      cpu: "1000m"
      memory: "1Gi"
```

### Connection Pool Formula:
$$\text{Total DB Connections} = (\text{API Pods} \times \text{POSTGRES\_POOL\_SIZE}) + (\text{Drain Workers} \times \text{POSTGRES\_POOL\_SIZE}) + (\text{Event Workers} \times 10)$$

* Default settings: $2 \times 20 + 2 \times 20 + 1 \times 10 = 90 \text{ connections}$, well within TimescaleDB's default `max_connections = 200`.

---

## ✅ Day 1 Sign-Off Checklist
- [x] Database connection pool parameters parameterized via `POSTGRES_POOL_SIZE` and `POSTGRES_MAX_OVERFLOW`.
- [x] Drain3 batch size & flush interval knobs parameterized in Helm ConfigMaps.
- [x] Kubernetes CPU/Memory requests & limits established in `values.yaml`.
- [x] Liveness & Readiness health probes integrated into API deployment manifests.
- [x] End-to-end load benchmarking script and DB profiler verified.
