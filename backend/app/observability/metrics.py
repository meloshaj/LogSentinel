"""Bounded application metrics and cached diagnostic update helpers.

The application already exposes the default Prometheus registry through
``prometheus-fastapi-instrumentator``.  The metric families in this module are
registered in that same default registry, so importing this module is enough to
make them available at ``/metrics`` once the parent application imports it.

The update functions are deliberately separate from request handlers.  Redis
stream sampling is an explicit, throttled operation intended for a periodic
task, while worker, ML, benchmarking, and WebSocket functions are cheap state
updates that can be called from their existing lifecycle boundaries.

All metric labels are fixed or bounded.  In particular, raw message IDs,
templates, users, request IDs, and exception text must never be supplied as
labels.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import threading
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prometheus_client import Counter, Gauge

# ---------------------------------------------------------------------------
# Metric families
# ---------------------------------------------------------------------------

STREAM_LENGTH = Gauge(
    "logsentinel_stream_length",
    "Current length of the ingestion Redis/Valkey stream.",
)
STREAM_PENDING_ENTRIES = Gauge(
    "logsentinel_stream_pending_entries",
    "Pending entries in the ingestion consumer group (XPENDING summary).",
)
STREAM_LAG = Gauge(
    "logsentinel_stream_lag",
    "Unconsumed consumer-group lag reported by XINFO GROUPS, or -1 when unavailable.",
)
STREAM_BACKLOG = Gauge(
    "logsentinel_stream_backlog",
    "Best available recoverable backlog: pending entries plus unconsumed lag when available.",
)
STREAM_DLQ_SIZE = Gauge(
    "logsentinel_stream_dlq_size",
    "Current length of the parser dead-letter Redis/Valkey stream.",
)
STREAM_LAG_AVAILABLE = Gauge(
    "logsentinel_stream_lag_available",
    "Whether the current stream sample included XINFO consumer-group lag.",
)
STREAM_REFRESH_SUCCESS = Gauge(
    "logsentinel_stream_metrics_refresh_success",
    "Whether the most recent stream metrics refresh succeeded.",
)
STREAM_REFRESH_ERRORS = Counter(
    "logsentinel_stream_metrics_refresh_errors_total",
    "Redis/Valkey stream metrics refresh failures.",
)
STREAM_LAST_REFRESH = Gauge(
    "logsentinel_stream_metrics_last_refresh_timestamp_seconds",
    "Unix timestamp of the most recent stream metrics refresh attempt.",
)
STREAM_REDIS_UP = Gauge(
    "logsentinel_stream_observability_up",
    "Whether Redis/Valkey was reachable during the most recent stream sample.",
)

_WORKER_LABELS = (
    "drain",
    "feature_extraction",
    "event_manager",
    "stream_cleaner",
    "retrain",
    "other",
)
WORKER_RUNNING = Gauge(
    "logsentinel_worker_running",
    "Whether the named in-process worker reports that it is running.",
    ["worker"],
)
WORKER_HEARTBEAT = Gauge(
    "logsentinel_worker_heartbeat_timestamp_seconds",
    "Most recent worker activity/heartbeat timestamp reported by the worker.",
    ["worker"],
)
WORKER_PROCESSED = Counter(
    "logsentinel_worker_processed_total",
    "Worker records processed according to its cumulative stats snapshot.",
    ["worker"],
)
WORKER_ERRORS = Counter(
    "logsentinel_worker_errors_total",
    "Worker processing errors according to its cumulative stats snapshot.",
    ["worker"],
)
WORKER_DLQ = Counter(
    "logsentinel_worker_dlq_total",
    "Worker messages routed to a dead-letter queue according to its stats snapshot.",
    ["worker"],
)
WORKER_QUEUE_SIZE = Gauge(
    "logsentinel_worker_queue_size",
    "Current bounded in-memory queue or buffer size reported by the worker.",
    ["worker"],
)

DRAIN3_TEMPLATE_CLUSTERS = Gauge(
    "logsentinel_drain3_template_clusters",
    "Current number of Drain3 template clusters.",
)
DRAIN3_TOTAL_CLUSTER_SIZE = Gauge(
    "logsentinel_drain3_total_cluster_size",
    "Total number of log messages represented by Drain3 clusters.",
)
DRAIN3_LOGS_PROCESSED = Counter(
    "logsentinel_drain3_logs_processed_total",
    "Logs processed by the Drain3 worker according to its cumulative stats.",
)
DRAIN3_PARSE_ERRORS = Counter(
    "logsentinel_drain3_parse_errors_total",
    "Drain3/parser errors according to the worker cumulative stats.",
)
DRAIN3_DLQ_EVENTS = Counter(
    "logsentinel_drain3_dlq_events_total",
    "Messages routed to the Drain3 dead-letter stream according to worker stats.",
)

ML_MODEL_LOADED = Gauge(
    "logsentinel_ml_model_loaded",
    "Whether the active Isolation Forest model is loaded and usable.",
)
ML_MODEL_AGE_SECONDS = Gauge(
    "logsentinel_ml_model_age_seconds",
    "Age in seconds of the active model artifact, or zero when unavailable.",
)
ML_MODEL_VERSION_INFO = Gauge(
    "logsentinel_ml_model_version_info",
    "Information gauge for the one active Isolation Forest model version.",
    ["version"],
)
ML_INFERENCE_TOTAL = Counter(
    "logsentinel_ml_inference_total",
    "Isolation Forest inference attempts.",
)
ML_INFERENCE_ERRORS = Counter(
    "logsentinel_ml_inference_errors_total",
    "Isolation Forest inference failures.",
)
ML_ANOMALIES = Counter(
    "logsentinel_ml_anomalies_total",
    "Anomalies emitted by successful Isolation Forest inferences.",
)

WEBSOCKET_CONNECTION_ATTEMPTS = Counter(
    "logsentinel_websocket_connection_attempts_total",
    "WebSocket handshake attempts recorded by the route integration hook.",
)
WEBSOCKET_AUTH_FAILURES = Counter(
    "logsentinel_websocket_authentication_failures_total",
    "WebSocket handshake authentication failures.",
)
WEBSOCKET_FRAMES_SENT = Counter(
    "logsentinel_websocket_frames_sent_total",
    "Successfully sent consolidated WebSocket telemetry frames.",
)
WEBSOCKET_SEND_FAILURES = Counter(
    "logsentinel_websocket_send_failures_total",
    "WebSocket telemetry frame send failures.",
)

BENCHMARK_THROUGHPUT = Gauge(
    "logsentinel_benchmark_throughput_logs_per_second",
    "Bounded in-memory benchmark throughput estimate.",
)
BENCHMARK_PIPELINE_LATENCY = Gauge(
    "logsentinel_benchmark_pipeline_latency_ms",
    "EMA of Drain3 pipeline processing latency in milliseconds.",
)
BENCHMARK_QUEUE_DEPTH = Gauge(
    "logsentinel_benchmark_queue_depth",
    "Current ingestion queue depth from the benchmarking collector.",
)
BENCHMARK_DB_BATCH_DURATION = Gauge(
    "logsentinel_benchmark_db_batch_duration_ms",
    "EMA of sink/database batch duration in milliseconds.",
)

ARCHIVE_JOBS_TOTAL = Counter(
    "logsentinel_archive_jobs_total",
    "Total archive jobs processed.",
)
ARCHIVE_FAILURES_TOTAL = Counter(
    "logsentinel_archive_failures_total",
    "Total archive jobs failed.",
)
ARCHIVE_BYTES_TOTAL = Counter(
    "logsentinel_archive_bytes_total",
    "Total bytes archived to S3.",
)
ARCHIVE_VERIFICATION_FAILURES = Counter(
    "logsentinel_archive_verification_failures_total",
    "Total verification failures for archived files.",
)
ARCHIVE_HOT_DELETE_FAILURES = Counter(
    "logsentinel_archive_hot_delete_failures_total",
    "Total failures dropping hot chunks.",
)
ARCHIVE_BACKLOG_SECONDS = Gauge(
    "logsentinel_archive_backlog_seconds",
    "Estimated backlog (oldest job age) for the archiver in seconds.",
)


# ---------------------------------------------------------------------------
# Cached state and small conversion helpers
# ---------------------------------------------------------------------------

_state_lock = threading.Lock()
_counter_snapshots: dict[tuple[str, str], float] = {}
_active_model_version: str | None = None

_stream_snapshot: dict[str, Any] = {
    "stream_length": 0,
    "pending_entries": 0,
    "lag": None,
    "backlog": 0,
    "dlq_size": 0,
    "lag_available": False,
    "redis_available": False,
    "last_refresh_at": None,
}
_worker_snapshot: dict[str, dict[str, Any]] = {}
_ml_snapshot: dict[str, Any] = {
    "model_loaded": False,
    "model_version": "unavailable",
    "model_age_seconds": 0.0,
    "inference_total": 0,
    "inference_errors_total": 0,
    "anomalies_total": 0,
}


def _finite_non_negative(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return max(0.0, number)


def _integer(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _mapping_value(mapping: Mapping[Any, Any], key: str, default: Any = None) -> Any:
    if key in mapping:
        return mapping[key]
    byte_key = key.encode("utf-8")
    return mapping.get(byte_key, default)


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _timestamp_seconds(value: Any) -> float | None:
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    elif isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.timestamp()


def _worker_label(worker: str) -> str:
    candidate = str(worker).strip().lower().replace("-", "_")
    return candidate if candidate in _WORKER_LABELS else "other"


def _counter_from_snapshot(
    metric: Counter,
    *,
    worker: str,
    field: str,
    value: Any,
) -> None:
    """Apply an absolute worker counter snapshot as a monotonic Prometheus delta."""
    current = _finite_non_negative(value)
    key = (field, worker)
    with _state_lock:
        previous = _counter_snapshots.get(key)
        if previous is None or current < previous:
            increment = current
        else:
            increment = current - previous
        _counter_snapshots[key] = current
    if increment > 0:
        metric.labels(worker=worker).inc(increment)


def _absolute_counter_from_snapshot(metric: Counter, *, field: str, value: Any) -> None:
    """Apply a label-free cumulative stats snapshot as a Prometheus delta."""
    current = _finite_non_negative(value)
    key = (f"absolute:{field}", "")
    with _state_lock:
        previous = _counter_snapshots.get(key)
        if previous is None or current < previous:
            increment = current
        else:
            increment = current - previous
        _counter_snapshots[key] = current
    if increment > 0:
        metric.inc(increment)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


# ---------------------------------------------------------------------------
# Redis/Valkey stream metrics
# ---------------------------------------------------------------------------


class StreamMetricsSampler:
    """Throttled sampler for one Redis Stream and its consumer group.

    ``refresh`` performs a bounded set of O(1) Redis commands (XLEN,
    XPENDING summary, and XINFO GROUPS) and updates the module metrics/cache.
    It is intended for a periodic background task, never for an HTTP request
    handler.  If XINFO lag is unavailable, ``backlog`` falls back to pending
    entries and ``lag_available`` makes that distinction explicit.
    """

    def __init__(self) -> None:
        self._last_refresh_monotonic: float | None = None
        self._refresh_lock: asyncio.Lock | None = None

    def _lock(self) -> asyncio.Lock:
        if self._refresh_lock is None:
            self._refresh_lock = asyncio.Lock()
        return self._refresh_lock

    async def refresh(
        self,
        redis_client: Any,
        *,
        stream_name: str,
        group_name: str,
        dlq_stream_name: str = "logs:dlq",
        min_interval_seconds: float = 5.0,
    ) -> dict[str, Any]:
        """Refresh the cached stream metrics and return a defensive snapshot."""
        async with self._lock():
            now_monotonic = time.monotonic()
            if (
                min_interval_seconds > 0
                and self._last_refresh_monotonic is not None
                and now_monotonic - self._last_refresh_monotonic < min_interval_seconds
            ):
                return get_stream_metrics_snapshot()

            refresh_time = time.time()
            STREAM_LAST_REFRESH.set(refresh_time)
            self._last_refresh_monotonic = now_monotonic

            try:
                stream_length = _integer(await _maybe_await(redis_client.xlen(stream_name)))
                dlq_size = _integer(await _maybe_await(redis_client.xlen(dlq_stream_name)))
                pending_entries = _pending_count(
                    await _maybe_await(redis_client.xpending(stream_name, group_name))
                )
                lag = await _group_lag(redis_client, stream_name, group_name)
                update_stream_metrics(
                    stream_length=stream_length,
                    pending_entries=pending_entries,
                    lag=lag,
                    dlq_size=dlq_size,
                    refreshed_at=refresh_time,
                    redis_available=True,
                )
                STREAM_REFRESH_SUCCESS.set(1)
                STREAM_REDIS_UP.set(1)
            except Exception:
                STREAM_REFRESH_ERRORS.inc()
                STREAM_REFRESH_SUCCESS.set(0)
                STREAM_REDIS_UP.set(0)
                with _state_lock:
                    _stream_snapshot["redis_available"] = False
                    _stream_snapshot["last_refresh_at"] = refresh_time

            return get_stream_metrics_snapshot()


def _pending_count(summary: Any) -> int:
    if isinstance(summary, Mapping):
        return _integer(_mapping_value(summary, "pending", 0))
    if isinstance(summary, (list, tuple)) and summary:
        return _integer(summary[0])
    return _integer(summary)


async def _group_lag(redis_client: Any, stream_name: str, group_name: str) -> int | None:
    """Return XINFO GROUPS lag, treating unsupported/old servers as unknown."""
    xinfo_groups = getattr(redis_client, "xinfo_groups", None)
    if xinfo_groups is None:
        return None
    try:
        groups = await _maybe_await(xinfo_groups(stream_name))
    except Exception:
        return None
    if not isinstance(groups, (list, tuple)):
        return None
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        if _text(_mapping_value(group, "name", "")) != group_name:
            continue
        value = _mapping_value(group, "lag")
        if value is None:
            return None
        return _integer(value)
    return None


stream_metrics_sampler = StreamMetricsSampler()


async def refresh_stream_metrics(
    redis_client: Any,
    *,
    stream_name: str,
    group_name: str,
    dlq_stream_name: str = "logs:dlq",
    min_interval_seconds: float = 5.0,
) -> dict[str, Any]:
    """Periodic-task API for refreshing stream length, PEL, lag, backlog, and DLQ."""
    return await stream_metrics_sampler.refresh(
        redis_client,
        stream_name=stream_name,
        group_name=group_name,
        dlq_stream_name=dlq_stream_name,
        min_interval_seconds=min_interval_seconds,
    )


def update_stream_metrics(
    *,
    stream_length: int,
    pending_entries: int,
    lag: int | None,
    dlq_size: int,
    refreshed_at: float | None = None,
    redis_available: bool = True,
) -> dict[str, Any]:
    """Update cached stream metrics from a precomputed, bounded observation.

    ``lag`` is the XINFO GROUPS value.  ``backlog`` includes pending entries
    and lag when lag is available; otherwise it equals pending entries.
    """
    stream_length_value = _integer(stream_length)
    pending_value = _integer(pending_entries)
    dlq_value = _integer(dlq_size)
    lag_value = _integer(lag) if lag is not None else None
    backlog = pending_value + lag_value if lag_value is not None else pending_value
    with _state_lock:
        _stream_snapshot.update(
            {
                "stream_length": stream_length_value,
                "pending_entries": pending_value,
                "lag": lag_value,
                "backlog": backlog,
                "dlq_size": dlq_value,
                "lag_available": lag_value is not None,
                "redis_available": bool(redis_available),
                "last_refresh_at": refreshed_at,
            }
        )

    STREAM_LENGTH.set(stream_length_value)
    STREAM_PENDING_ENTRIES.set(pending_value)
    STREAM_LAG.set(lag_value if lag_value is not None else -1)
    STREAM_BACKLOG.set(backlog)
    STREAM_DLQ_SIZE.set(dlq_value)
    STREAM_LAG_AVAILABLE.set(1 if lag_value is not None else 0)
    STREAM_REDIS_UP.set(1 if redis_available else 0)
    return get_stream_metrics_snapshot()


def get_stream_metrics_snapshot() -> dict[str, Any]:
    """Return the last stream observation without issuing Redis commands."""
    with _state_lock:
        return dict(_stream_snapshot)


# ---------------------------------------------------------------------------
# Worker and Drain3 diagnostics
# ---------------------------------------------------------------------------


def observe_worker_stats(
    worker: str,
    stats: Mapping[str, Any],
    *,
    processed_key: str = "processed_count",
    errors_key: str = "error_count",
    dlq_key: str = "dlq_count",
    queue_key: str = "queue_size",
    heartbeat_keys: tuple[str, ...] = ("last_processed_at", "last_heartbeat_at"),
) -> dict[str, Any]:
    """Export a bounded worker ``get_stats()`` snapshot without raw labels."""
    label = _worker_label(worker)
    running = stats.get("running")
    if running is not None:
        WORKER_RUNNING.labels(worker=label).set(1 if bool(running) else 0)

    heartbeat = None
    for key in heartbeat_keys:
        heartbeat = _timestamp_seconds(stats.get(key))
        if heartbeat is not None:
            break
    if heartbeat is not None:
        WORKER_HEARTBEAT.labels(worker=label).set(heartbeat)

    if stats.get(processed_key) is not None:
        _counter_from_snapshot(
            WORKER_PROCESSED,
            worker=label,
            field="processed",
            value=stats.get(processed_key),
        )
    if stats.get(errors_key) is not None:
        _counter_from_snapshot(
            WORKER_ERRORS,
            worker=label,
            field="errors",
            value=stats.get(errors_key),
        )
    if stats.get(dlq_key) is not None:
        _counter_from_snapshot(
            WORKER_DLQ,
            worker=label,
            field="dlq",
            value=stats.get(dlq_key),
        )
    if stats.get(queue_key) is not None:
        WORKER_QUEUE_SIZE.labels(worker=label).set(_finite_non_negative(stats.get(queue_key)))

    snapshot = {
        "running": bool(running) if running is not None else None,
        "heartbeat_timestamp_seconds": heartbeat,
        "processed": _finite_non_negative(stats.get(processed_key)) if stats.get(processed_key) is not None else None,
        "errors": _finite_non_negative(stats.get(errors_key)) if stats.get(errors_key) is not None else None,
        "dlq": _finite_non_negative(stats.get(dlq_key)) if stats.get(dlq_key) is not None else None,
        "queue_size": _finite_non_negative(stats.get(queue_key)) if stats.get(queue_key) is not None else None,
    }
    with _state_lock:
        _worker_snapshot[label] = snapshot
    return dict(snapshot)


def record_worker_heartbeat(worker: str, *, running: bool = True, observed_at: float | None = None) -> None:
    """Record an explicit worker-loop heartbeat from a real lifecycle boundary."""
    label = _worker_label(worker)
    timestamp = time.time() if observed_at is None else _finite_non_negative(observed_at)
    WORKER_RUNNING.labels(worker=label).set(1 if running else 0)
    WORKER_HEARTBEAT.labels(worker=label).set(timestamp)
    with _state_lock:
        snapshot = _worker_snapshot.setdefault(label, {})
        snapshot.update({"running": running, "heartbeat_timestamp_seconds": timestamp})


def record_drain_worker_stats(
    stats: Mapping[str, Any],
    *,
    parser_stats: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Export the existing DrainWorker and DrainParser diagnostic snapshots."""
    snapshot = observe_worker_stats("drain", stats)
    if parser_stats is not None:
        DRAIN3_TEMPLATE_CLUSTERS.set(_finite_non_negative(parser_stats.get("cluster_count")))
        DRAIN3_TOTAL_CLUSTER_SIZE.set(_finite_non_negative(parser_stats.get("total_cluster_size")))
    _absolute_counter_from_snapshot(
        DRAIN3_LOGS_PROCESSED,
        field="drain3_processed",
        value=stats.get("processed_count"),
    )
    _absolute_counter_from_snapshot(
        DRAIN3_PARSE_ERRORS,
        field="drain3_errors",
        value=stats.get("error_count"),
    )
    _absolute_counter_from_snapshot(
        DRAIN3_DLQ_EVENTS,
        field="drain3_dlq",
        value=stats.get("dlq_count"),
    )
    return snapshot


def record_feature_worker_stats(stats: Mapping[str, Any]) -> dict[str, Any]:
    """Export FeatureExtractionWorker stats using the same bounded worker API."""
    return observe_worker_stats(
        "feature_extraction",
        stats,
        processed_key="features_extracted",
        errors_key="extraction_errors",
        dlq_key="missing_dlq_count",
        queue_key="feature_buffer_size",
        heartbeat_keys=("last_extraction_at", "last_heartbeat_at"),
    )


def get_worker_metrics_snapshot() -> dict[str, dict[str, Any]]:
    """Return cached worker state without inspecting any worker or queue."""
    with _state_lock:
        return {name: dict(value) for name, value in _worker_snapshot.items()}


# ---------------------------------------------------------------------------
# ML and benchmark diagnostics
# ---------------------------------------------------------------------------


def set_ml_status(
    *,
    loaded: bool,
    model_version: str | None = None,
    model_path: str | Path | None = None,
    model_age_seconds: float | None = None,
    inference_total: float | None = None,
    inference_errors_total: float | None = None,
    anomalies_total: float | None = None,
) -> dict[str, Any]:
    """Publish the active model state, including an operator-visible loaded bit."""
    global _active_model_version

    age = model_age_seconds
    if age is None and model_path is not None:
        try:
            age = max(0.0, time.time() - Path(model_path).stat().st_mtime)
        except (FileNotFoundError, OSError):
            age = 0.0
    age_value = _finite_non_negative(age)
    version = str(model_version or ("unavailable" if not loaded else "unknown"))[:64]

    ML_MODEL_LOADED.set(1 if loaded else 0)
    ML_MODEL_AGE_SECONDS.set(age_value)
    if _active_model_version != version:
        if _active_model_version is not None:
            try:
                ML_MODEL_VERSION_INFO.remove(_active_model_version)
            except KeyError:
                pass
        ML_MODEL_VERSION_INFO.labels(version=version).set(1)
        _active_model_version = version

    with _state_lock:
        _ml_snapshot.update(
            {
                "model_loaded": bool(loaded),
                "model_version": version,
                "model_age_seconds": age_value,
            }
        )

    # Detector health exposes cumulative counters. Convert those absolute
    # snapshots into Prometheus deltas so periodic sampling is idempotent.
    if inference_total is not None:
        _absolute_counter_from_snapshot(
            ML_INFERENCE_TOTAL,
            field="ml_inference_total",
            value=inference_total,
        )
        with _state_lock:
            _ml_snapshot["inference_total"] = _integer(inference_total)
    if inference_errors_total is not None:
        _absolute_counter_from_snapshot(
            ML_INFERENCE_ERRORS,
            field="ml_inference_errors_total",
            value=inference_errors_total,
        )
        with _state_lock:
            _ml_snapshot["inference_errors_total"] = _integer(inference_errors_total)
    if anomalies_total is not None:
        _absolute_counter_from_snapshot(
            ML_ANOMALIES,
            field="ml_anomalies_total",
            value=anomalies_total,
        )
        with _state_lock:
            _ml_snapshot["anomalies_total"] = _integer(anomalies_total)

    with _state_lock:
        return dict(_ml_snapshot)


def set_ml_status_from_detector(
    detector: Any,
    *,
    model_path: str | Path | None = None,
) -> dict[str, Any]:
    """Adapt the existing IsolationForest detector object to ``set_ml_status``."""
    loaded = detector is not None and getattr(detector, "model", None) is not None
    return set_ml_status(
        loaded=loaded,
        model_version=getattr(detector, "model_version", None),
        model_path=model_path,
    )


def observe_ml_inference(*, success: bool, is_anomaly: bool = False) -> None:
    """Record one inference attempt and, on success, an optional anomaly."""
    ML_INFERENCE_TOTAL.inc()
    with _state_lock:
        _ml_snapshot["inference_total"] = int(_ml_snapshot["inference_total"]) + 1
    if not success:
        ML_INFERENCE_ERRORS.inc()
        with _state_lock:
            _ml_snapshot["inference_errors_total"] = int(_ml_snapshot["inference_errors_total"]) + 1
        return
    if is_anomaly:
        ML_ANOMALIES.inc()
        with _state_lock:
            _ml_snapshot["anomalies_total"] = int(_ml_snapshot["anomalies_total"]) + 1


def get_ml_metrics_snapshot() -> dict[str, Any]:
    """Return cached ML status and counters without touching the model."""
    with _state_lock:
        return dict(_ml_snapshot)


def observe_benchmarking_snapshot(snapshot: Mapping[str, Any]) -> None:
    """Export the existing O(1) BenchmarkingCollector health snapshot."""
    BENCHMARK_THROUGHPUT.set(_finite_non_negative(snapshot.get("throughput_logs_per_sec")))
    BENCHMARK_PIPELINE_LATENCY.set(_finite_non_negative(snapshot.get("pipeline_latency_ms")))
    BENCHMARK_QUEUE_DEPTH.set(_finite_non_negative(snapshot.get("queue_depth")))
    BENCHMARK_DB_BATCH_DURATION.set(_finite_non_negative(snapshot.get("db_batch_duration_ms")))


# ---------------------------------------------------------------------------
# WebSocket delivery hooks
# ---------------------------------------------------------------------------


def record_websocket_connection_attempt() -> None:
    """Record one route-level handshake attempt; no token or identity is stored."""
    WEBSOCKET_CONNECTION_ATTEMPTS.inc()


def record_websocket_authentication_failure() -> None:
    """Record one rejected WebSocket authentication attempt."""
    WEBSOCKET_AUTH_FAILURES.inc()


def record_websocket_frame_sent() -> None:
    """Record one successfully delivered consolidated frame."""
    WEBSOCKET_FRAMES_SENT.inc()


def record_websocket_send_failure() -> None:
    """Record one failed WebSocket frame send."""
    WEBSOCKET_SEND_FAILURES.inc()


def get_websocket_metrics_snapshot() -> dict[str, float]:
    """Return WebSocket counters for deterministic tests and local diagnostics."""
    return {
        "connection_attempts_total": float(WEBSOCKET_CONNECTION_ATTEMPTS._value.get()),
        "authentication_failures_total": float(WEBSOCKET_AUTH_FAILURES._value.get()),
        "frames_sent_total": float(WEBSOCKET_FRAMES_SENT._value.get()),
        "send_failures_total": float(WEBSOCKET_SEND_FAILURES._value.get()),
    }
