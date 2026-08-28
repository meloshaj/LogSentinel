from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

import asyncpg
import httpx
import psutil


DEFAULT_CONCURRENCY_LEVELS = [10, 50, 100]
DEFAULT_TOTAL_RECORDS = 10_000
DEFAULT_OUTPUT_PATH = Path("benchmark_results.json")
RETRYABLE_STATUSES = {429, 503}
SUCCESS_STATUSES = {200, 201, 202}


@dataclass(frozen=True)
class BenchmarkLogRecord:
    index: int
    category: str
    anomaly_type: str | None
    timestamp: str
    service_name: str
    level: str
    message: str
    raw: str
    metadata: dict[str, Any]


@dataclass
class ResourceSnapshot:
    elapsed_seconds: float
    rss_bytes: int
    cpu_percent: float


@dataclass
class RequestMetric:
    correlation_id: str
    status_code: int | None
    latency_ms: float
    accepted: bool
    attempt_count: int
    error: str | None = None


@dataclass
class TemplateSample:
    elapsed_seconds: float
    template_count: int
    processed_count: int


@dataclass
class BenchmarkRunResult:
    run_id: str
    concurrency: int
    total_records: int
    successful_requests: int
    failed_requests: int
    accepted_requests: int
    rejected_requests: int
    status_distribution: dict[str, int]
    duration_seconds: float
    ingestion_throughput_logs_per_second: float
    processing_throughput_logs_per_second: float
    http_latency_ms: dict[str, float]
    e2e_persistence_latency_ms: dict[str, float]
    resource_utilization: dict[str, float]
    drain3: dict[str, Any]
    errors: list[str] = field(default_factory=list)


class ResourceMonitor:
    def __init__(self, pid: int | None = None, interval_seconds: float = 0.25) -> None:
        self.process = psutil.Process(pid or os.getpid())
        self.interval_seconds = interval_seconds
        self.snapshots: list[ResourceSnapshot] = []
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def __aenter__(self) -> "ResourceMonitor":
        self.start()
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        await self.stop()

    def start(self) -> None:
        self._running = True
        self.process.cpu_percent(interval=None)
        self._task = asyncio.create_task(self._sample_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is None:
            return
        await self._task
        self._task = None

    async def _sample_loop(self) -> None:
        started_at = time.perf_counter()
        while self._running:
            try:
                memory = self.process.memory_info()
                cpu = self.process.cpu_percent(interval=None)
                self.snapshots.append(
                    ResourceSnapshot(
                        elapsed_seconds=time.perf_counter() - started_at,
                        rss_bytes=int(memory.rss),
                        cpu_percent=float(cpu),
                    )
                )
            except psutil.Error:
                break
            await asyncio.sleep(self.interval_seconds)

    def summary(self) -> dict[str, float]:
        if not self.snapshots:
            return {
                "peak_rss_mb": 0.0,
                "average_rss_mb": 0.0,
                "peak_cpu_percent": 0.0,
                "average_cpu_percent": 0.0,
            }

        rss_values = [snapshot.rss_bytes / (1024 * 1024) for snapshot in self.snapshots]
        cpu_values = [snapshot.cpu_percent for snapshot in self.snapshots]
        return {
            "peak_rss_mb": round(max(rss_values), 2),
            "average_rss_mb": round(statistics.fmean(rss_values), 2),
            "peak_cpu_percent": round(max(cpu_values), 2),
            "average_cpu_percent": round(statistics.fmean(cpu_values), 2),
        }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = (len(ordered) - 1) * (percent / 100.0)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return round(ordered[low], 3)
    weighted = ordered[low] * (high - rank) + ordered[high] * (rank - low)
    return round(weighted, 3)


def latency_profile(values: list[float]) -> dict[str, float]:
    return {
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "min": round(min(values), 3) if values else 0.0,
        "max": round(max(values), 3) if values else 0.0,
        "average": round(statistics.fmean(values), 3) if values else 0.0,
    }


def generate_dataset(total_records: int, *, run_id: str, seed: int) -> list[BenchmarkLogRecord]:
    rng = random.Random(seed)
    normal_count = int(total_records * 0.9)
    anomaly_count = total_records - normal_count
    records: list[BenchmarkLogRecord] = []

    services = ["api-gateway", "auth-service", "orders", "payments", "inventory-db"]
    normal_templates = [
        ("info", "{service} request {request_id} completed in {latency_ms}ms status={status}"),
        ("info", "{service} cache lookup key={cache_key} hit={cache_hit} duration={latency_ms}ms"),
        ("info", "{service} processed order {order_id} user={user_id} total=${total}"),
        ("warning", "{service} retry attempt {attempt}/3 for dependency={dependency} duration={latency_ms}ms"),
        ("info", "{service} health check passed region={region} latency={latency_ms}ms"),
    ]
    anomaly_templates = [
        (
            "sql_injection",
            "warning",
            "{service} blocked suspicious query input=\"' OR 1=1; DROP TABLE users; --\" "
            "client_ip={client_ip} path=/api/v1/search",
        ),
        (
            "stack_trace",
            "error",
            "{service} unhandled exception RuntimeError: payment reconciliation failed\n"
            "Traceback (most recent call last):\n"
            "  File \"/srv/app/handlers.py\", line {line_no}, in handle\n"
            "    await repository.commit()\n"
            "RuntimeError: transaction state invalid for request {request_id}",
        ),
        (
            "latency_spike",
            "warning",
            "{service} latency spike detected route={route} duration={latency_ms}ms "
            "baseline_ms={baseline_ms} saturation={saturation_pct}%",
        ),
    ]

    for index in range(normal_count):
        service = rng.choice(services)
        level, template = rng.choice(normal_templates)
        latency_ms = rng.randint(8, 250)
        message = template.format(
            service=service,
            request_id=f"{run_id}-req-{index}",
            latency_ms=latency_ms,
            status=rng.choice([200, 200, 201, 204, 304]),
            cache_key=f"product:{rng.randint(1000, 9999)}",
            cache_hit=rng.choice(["true", "false"]),
            order_id=f"ORD-{rng.randint(2026000000, 2026999999)}",
            user_id=f"usr_{rng.randint(100000, 999999)}",
            total=round(rng.uniform(9.99, 999.99), 2),
            attempt=rng.randint(1, 3),
            dependency=rng.choice(["postgres", "redis", "stripe", "inventory"]),
            region=rng.choice(["eu-central-1", "us-east-1", "us-west-2"]),
        )
        timestamp = utc_now_iso()
        records.append(
            BenchmarkLogRecord(
                index=index,
                category="normal",
                anomaly_type=None,
                timestamp=timestamp,
                service_name=service,
                level=level,
                message=message,
                raw=f"{timestamp} {level.upper()} {service}: {message}",
                metadata={
                    "benchmark_run_id": run_id,
                    "dataset_index": index,
                    "category": "normal",
                    "sent_at": None,
                    "synthetic_latency_ms": latency_ms,
                },
            )
        )

    for offset in range(anomaly_count):
        index = normal_count + offset
        service = rng.choice(services)
        anomaly_type, level, template = anomaly_templates[offset % len(anomaly_templates)]
        latency_ms = rng.randint(2_500, 15_000)
        message = template.format(
            service=service,
            client_ip=f"203.0.113.{rng.randint(1, 254)}",
            line_no=rng.randint(40, 400),
            request_id=f"{run_id}-anom-{offset}",
            route=rng.choice(["/api/v1/checkout", "/api/v1/orders", "/api/v1/auth/token"]),
            latency_ms=latency_ms,
            baseline_ms=rng.randint(45, 120),
            saturation_pct=rng.randint(90, 100),
        )
        timestamp = utc_now_iso()
        records.append(
            BenchmarkLogRecord(
                index=index,
                category="anomaly",
                anomaly_type=anomaly_type,
                timestamp=timestamp,
                service_name=service,
                level=level,
                message=message,
                raw=f"{timestamp} {level.upper()} {service}: {message}",
                metadata={
                    "benchmark_run_id": run_id,
                    "dataset_index": index,
                    "category": "anomaly",
                    "anomaly_type": anomaly_type,
                    "sent_at": None,
                    "synthetic_latency_ms": latency_ms,
                },
            )
        )

    rng.shuffle(records)
    return records


def build_payload(record: BenchmarkLogRecord, *, run_id: str) -> dict[str, Any]:
    sent_at = utc_now_iso()
    metadata = dict(record.metadata)
    metadata["sent_at"] = sent_at
    return {
        "source": "benchmark-performance",
        "environment": "benchmark",
        "correlation_id": f"{run_id}-{record.index}",
        "logs": [
            {
                "timestamp": record.timestamp,
                "service_name": record.service_name,
                "level": record.level,
                "message": record.message,
                "raw": record.raw,
                "metadata": metadata,
            }
        ],
    }


def status_distribution(metrics: list[RequestMetric]) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for metric in metrics:
        key = str(metric.status_code) if metric.status_code is not None else "exception"
        distribution[key] = distribution.get(key, 0) + 1
    return dict(sorted(distribution.items()))


def normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


async def fetch_json(client: httpx.AsyncClient, path: str) -> dict[str, Any]:
    response = await client.get(path)
    response.raise_for_status()
    return response.json()


async def poll_template_samples(
    client: httpx.AsyncClient,
    stop_event: asyncio.Event,
    *,
    interval_seconds: float,
) -> list[TemplateSample]:
    samples: list[TemplateSample] = []
    started_at = time.perf_counter()
    while not stop_event.is_set():
        try:
            stats = await fetch_json(client, "/drain3/stats")
            parser = stats.get("parser", {})
            worker = stats.get("worker", {})
            samples.append(
                TemplateSample(
                    elapsed_seconds=time.perf_counter() - started_at,
                    template_count=int(parser.get("cluster_count", 0) or 0),
                    processed_count=int(worker.get("processed_count", 0) or 0),
                )
            )
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue
    return samples


async def wait_for_processing(
    client: httpx.AsyncClient,
    *,
    baseline_processed_count: int,
    accepted_count: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_stats: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last_stats = await fetch_json(client, "/drain3/stats")
        worker = last_stats.get("worker", {})
        processed = int(worker.get("processed_count", 0) or 0)
        queue_size = int(worker.get("queue_size", 0) or 0)
        if processed - baseline_processed_count >= accepted_count and queue_size == 0:
            return last_stats
        await asyncio.sleep(0.25)
    raise TimeoutError(
        "Timed out waiting for Drain3 worker completion; "
        f"accepted_count={accepted_count} last_stats={last_stats}"
    )


async def flush_pipeline(client: httpx.AsyncClient) -> None:
    response = await client.post("/drain3/flush")
    response.raise_for_status()


async def send_record(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
    *,
    max_retries: int,
) -> RequestMetric:
    correlation_id = str(payload["correlation_id"])
    for attempt in range(1, max_retries + 1):
        started_at = time.perf_counter()
        try:
            response = await client.post("/ingest-log", json=payload)
            latency_ms = (time.perf_counter() - started_at) * 1000.0
            accepted = False
            try:
                accepted = bool(response.json().get("accepted", False))
            except ValueError:
                accepted = False

            if response.status_code in SUCCESS_STATUSES:
                return RequestMetric(
                    correlation_id=correlation_id,
                    status_code=response.status_code,
                    latency_ms=latency_ms,
                    accepted=accepted,
                    attempt_count=attempt,
                )

            if response.status_code in RETRYABLE_STATUSES and attempt < max_retries:
                await asyncio.sleep(min(0.05 * (2 ** (attempt - 1)), 1.0))
                continue

            return RequestMetric(
                correlation_id=correlation_id,
                status_code=response.status_code,
                latency_ms=latency_ms,
                accepted=accepted,
                attempt_count=attempt,
                error=response.text[:500],
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - started_at) * 1000.0
            if attempt < max_retries:
                await asyncio.sleep(min(0.05 * (2 ** (attempt - 1)), 1.0))
                continue
            return RequestMetric(
                correlation_id=correlation_id,
                status_code=None,
                latency_ms=latency_ms,
                accepted=False,
                attempt_count=attempt,
                error=f"{type(exc).__name__}: {exc}",
            )

    raise RuntimeError("unreachable")


async def run_load_phase(
    client: httpx.AsyncClient,
    dataset: list[BenchmarkLogRecord],
    *,
    run_id: str,
    concurrency: int,
    max_retries: int,
) -> list[RequestMetric]:
    queue: asyncio.Queue[BenchmarkLogRecord | None] = asyncio.Queue()
    metrics: list[RequestMetric] = []
    metrics_lock = asyncio.Lock()

    for record in dataset:
        await queue.put(record)
    for _ in range(concurrency):
        await queue.put(None)

    async def worker() -> None:
        while True:
            record = await queue.get()
            try:
                if record is None:
                    return
                metric = await send_record(
                    client,
                    build_payload(record, run_id=run_id),
                    max_retries=max_retries,
                )
                async with metrics_lock:
                    metrics.append(metric)
            finally:
                queue.task_done()

    await asyncio.gather(*(worker() for _ in range(concurrency)))
    return metrics


def postgres_settings_from_env() -> dict[str, Any] | None:
    raw_url = os.getenv("BENCHMARK_DATABASE_URL") or os.getenv("DATABASE_URL")
    if raw_url:
        parsed = urlparse(raw_url.replace("postgresql+asyncpg://", "postgresql://", 1))
        query = parse_qs(parsed.query)
        kwargs: dict[str, Any] = {
            "user": unquote(parsed.username or ""),
            "password": unquote(parsed.password or ""),
            "host": parsed.hostname or "127.0.0.1",
            "port": parsed.port or 5432,
            "database": (parsed.path or "/logsentinel_db").lstrip("/"),
            "timeout": 5.0,
            "command_timeout": 30.0,
        }
        if query.get("ssl", ["disable"])[0] == "disable":
            kwargs["ssl"] = False
        return kwargs

    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    database = os.getenv("POSTGRES_DB")
    if not (user and password and database):
        return None

    return {
        "user": user,
        "password": password,
        "host": os.getenv("POSTGRES_HOST", "127.0.0.1"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "database": database,
        "timeout": 5.0,
        "command_timeout": 30.0,
        "ssl": False,
    }


async def fetch_persistence_latencies(run_id: str) -> tuple[list[float], int]:
    settings = postgres_settings_from_env()
    if settings is None:
        return [], 0

    try:
        connection = await asyncpg.connect(**settings)
    except Exception:
        return [], 0

    try:
        rows = await connection.fetch(
            """
            SELECT created_at, metadata
            FROM logs
            WHERE correlation_id LIKE $1
            """,
            f"{run_id}-%",
        )
    finally:
        await connection.close()

    latencies: list[float] = []
    for row in rows:
        metadata = row["metadata"] or {}
        sent_at = metadata.get("sent_at")
        if not sent_at:
            continue
        created_at = parse_datetime(row["created_at"])
        sent_at_dt = parse_datetime(str(sent_at))
        latencies.append(max(0.0, (created_at - sent_at_dt).total_seconds() * 1000.0))
    return latencies, len(rows)


def convergence_metrics(samples: list[TemplateSample]) -> dict[str, Any]:
    if not samples:
        return {
            "template_count": 0,
            "samples": [],
            "convergence_rate_templates_per_1k_logs": 0.0,
        }
    first = samples[0]
    last = samples[-1]
    processed_delta = max(0, last.processed_count - first.processed_count)
    template_delta = max(0, last.template_count - first.template_count)
    rate = (template_delta / processed_delta * 1000.0) if processed_delta else 0.0
    return {
        "template_count": last.template_count,
        "samples": [asdict(sample) for sample in samples],
        "convergence_rate_templates_per_1k_logs": round(rate, 3),
    }


async def run_benchmark_for_concurrency(
    *,
    base_url: str,
    api_key: str,
    total_records: int,
    concurrency: int,
    seed: int,
    timeout_seconds: float,
    processing_timeout_seconds: float,
    max_retries: int,
    monitor_pid: int | None,
) -> BenchmarkRunResult:
    run_id = f"benchmark-{concurrency}-{uuid4().hex}"
    dataset = generate_dataset(total_records, run_id=run_id, seed=seed + concurrency)
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key,
    }
    limits = httpx.Limits(
        max_connections=max(concurrency, 1),
        max_keepalive_connections=max(concurrency, 1),
    )
    timeout = httpx.Timeout(timeout_seconds, connect=5.0, pool=timeout_seconds)

    async with httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        limits=limits,
        timeout=timeout,
    ) as client:
        baseline_stats = await fetch_json(client, "/drain3/stats")
        baseline_processed = int(
            baseline_stats.get("worker", {}).get("processed_count", 0) or 0
        )
        stop_sampling = asyncio.Event()
        template_task = asyncio.create_task(
            poll_template_samples(client, stop_sampling, interval_seconds=1.0)
        )

        started_at = time.perf_counter()
        async with ResourceMonitor(pid=monitor_pid) as monitor:
            metrics = await run_load_phase(
                client,
                dataset,
                run_id=run_id,
                concurrency=concurrency,
                max_retries=max_retries,
            )
            ingest_finished_at = time.perf_counter()
            accepted_count = sum(1 for metric in metrics if metric.accepted)
            final_stats = await wait_for_processing(
                client,
                baseline_processed_count=baseline_processed,
                accepted_count=accepted_count,
                timeout_seconds=processing_timeout_seconds,
            )
            await flush_pipeline(client)
        stop_sampling.set()
        samples = await template_task

    total_duration = time.perf_counter() - started_at
    ingest_duration = max(ingest_finished_at - started_at, 0.001)
    http_latencies = [metric.latency_ms for metric in metrics]
    e2e_latencies, persisted_count = await fetch_persistence_latencies(run_id)
    errors = sorted(
        {
            str(metric.error)
            for metric in metrics
            if metric.error
        }
    )[:20]

    accepted_count = sum(1 for metric in metrics if metric.accepted)
    successful_requests = sum(1 for metric in metrics if metric.status_code in SUCCESS_STATUSES)
    rejected_requests = len(metrics) - accepted_count
    processed_delta = (
        int(final_stats.get("worker", {}).get("processed_count", 0) or 0)
        - baseline_processed
    )

    return BenchmarkRunResult(
        run_id=run_id,
        concurrency=concurrency,
        total_records=total_records,
        successful_requests=successful_requests,
        failed_requests=len(metrics) - successful_requests,
        accepted_requests=accepted_count,
        rejected_requests=rejected_requests,
        status_distribution=status_distribution(metrics),
        duration_seconds=round(total_duration, 3),
        ingestion_throughput_logs_per_second=round(total_records / ingest_duration, 2),
        processing_throughput_logs_per_second=round(
            processed_delta / total_duration if total_duration > 0 else 0.0,
            2,
        ),
        http_latency_ms=latency_profile(http_latencies),
        e2e_persistence_latency_ms=latency_profile(e2e_latencies),
        resource_utilization=monitor.summary(),
        drain3={
            **convergence_metrics(samples),
            "processed_count_delta": processed_delta,
            "persisted_rows_observed": persisted_count,
        },
        errors=errors,
    )


def markdown_summary(results: list[BenchmarkRunResult]) -> str:
    lines = [
        "",
        "| Concurrency | Requests | Accepted | Rejected | Ingest logs/s | Process logs/s | HTTP p50 ms | HTTP p95 ms | HTTP p99 ms | E2E p95 ms | Templates | Peak RSS MB | Peak CPU % |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        lines.append(
            "| "
            f"{result.concurrency} | "
            f"{result.total_records} | "
            f"{result.accepted_requests} | "
            f"{result.rejected_requests} | "
            f"{result.ingestion_throughput_logs_per_second:.2f} | "
            f"{result.processing_throughput_logs_per_second:.2f} | "
            f"{result.http_latency_ms['p50']:.2f} | "
            f"{result.http_latency_ms['p95']:.2f} | "
            f"{result.http_latency_ms['p99']:.2f} | "
            f"{result.e2e_persistence_latency_ms['p95']:.2f} | "
            f"{result.drain3['template_count']} | "
            f"{result.resource_utilization['peak_rss_mb']:.2f} | "
            f"{result.resource_utilization['peak_cpu_percent']:.2f} |"
        )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run reproducible LogSentinel ingestion performance benchmarks.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("LOGSENTINEL_BASE_URL", "http://127.0.0.1:8000"),
        help="Base URL for the running LogSentinel backend.",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("INGEST_API_KEY", "dev-local-key"),
        help="Ingestion API key sent as X-API-Key.",
    )
    parser.add_argument(
        "--total-records",
        type=int,
        default=DEFAULT_TOTAL_RECORDS,
        help="Synthetic log records per concurrency run.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        nargs="+",
        default=DEFAULT_CONCURRENCY_LEVELS,
        help="Concurrent worker counts to benchmark.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="JSON output path.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Deterministic synthetic data seed.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
        help="Per-request timeout.",
    )
    parser.add_argument(
        "--processing-timeout-seconds",
        type=float,
        default=180.0,
        help="Maximum wait for accepted logs to drain through Drain3.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Retry attempts for 429/503 or transient HTTP exceptions.",
    )
    parser.add_argument(
        "--monitor-pid",
        type=int,
        default=None,
        help="Optional backend PID to monitor. Defaults to this benchmark process.",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    if args.total_records <= 0:
        raise SystemExit("--total-records must be positive")
    if any(value <= 0 for value in args.concurrency):
        raise SystemExit("--concurrency values must be positive")

    base_url = normalize_base_url(args.base_url)
    results: list[BenchmarkRunResult] = []
    started_at = utc_now_iso()

    print(f"LogSentinel benchmark started at {started_at}")
    print(f"Target: {base_url}")
    print(f"Records per run: {args.total_records:,}")
    print(f"Concurrency levels: {', '.join(str(value) for value in args.concurrency)}")

    for concurrency in args.concurrency:
        print(f"\nRunning concurrency={concurrency} ...", flush=True)
        result = await run_benchmark_for_concurrency(
            base_url=base_url,
            api_key=args.api_key,
            total_records=args.total_records,
            concurrency=concurrency,
            seed=args.seed,
            timeout_seconds=args.timeout_seconds,
            processing_timeout_seconds=args.processing_timeout_seconds,
            max_retries=args.max_retries,
            monitor_pid=args.monitor_pid,
        )
        results.append(result)
        print(
            f"Completed concurrency={concurrency}: "
            f"{result.processing_throughput_logs_per_second:.2f} processed logs/s"
        )

    output = {
        "benchmark": {
            "started_at": started_at,
            "finished_at": utc_now_iso(),
            "target_base_url": base_url,
            "total_records_per_run": args.total_records,
            "dataset_split": {
                "normal_percent": 90,
                "anomaly_percent": 10,
                "anomaly_patterns": ["sql_injection", "stack_trace", "latency_spike"],
            },
            "concurrency_levels": args.concurrency,
            "monitor_pid": args.monitor_pid or os.getpid(),
        },
        "runs": [asdict(result) for result in results],
    }
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")

    print(markdown_summary(results))
    print(f"\nRaw metrics written to {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nBenchmark interrupted.", file=sys.stderr)
        raise SystemExit(130)
