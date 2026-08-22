"""Focused regression coverage for the Audit 5 observability slice."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from prometheus_client import generate_latest

import backend.app.main as main_module
from backend.app.observability.metrics import (
    StreamMetricsSampler,
    get_ml_metrics_snapshot,
    get_stream_metrics_snapshot,
    get_websocket_metrics_snapshot,
    get_worker_metrics_snapshot,
    observe_ml_inference,
    record_drain_worker_stats,
    record_feature_worker_stats,
    set_ml_status,
    update_stream_metrics,
)
from backend.app.services.batch_manager import ParsedLogBatchManager
from backend.app.services.benchmarking import BenchmarkingCollector
from backend.app.websockets.broadcaster import HighLoadBroadcaster


class FakeRedis:
    def __init__(self, *, stream_length: int = 10, dlq_size: int = 2, pending: int = 4, lag: int | None = 6) -> None:
        self.stream_length = stream_length
        self.dlq_size = dlq_size
        self.pending = pending
        self.lag = lag
        self.calls: list[str] = []

    async def xlen(self, stream_name: str) -> int:
        self.calls.append(f"xlen:{stream_name}")
        return self.dlq_size if stream_name == "logs:dlq" else self.stream_length

    async def xpending(self, stream_name: str, group_name: str) -> dict[str, int]:
        self.calls.append(f"xpending:{stream_name}:{group_name}")
        return {"pending": self.pending}

    async def xinfo_groups(self, stream_name: str) -> list[dict[str, Any]]:
        self.calls.append(f"xinfo_groups:{stream_name}")
        return [{"name": "log_workers", "lag": self.lag}]


class FailingRedis:
    async def xlen(self, _: str) -> int:
        raise ConnectionError("Redis unavailable")


class FakeWebSocket:
    def __init__(self, *, fail_send: bool = False) -> None:
        self.accepted = False
        self.fail_send = fail_send
        self.sent_events: list[dict[str, Any]] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, event: dict[str, Any]) -> None:
        if self.fail_send:
            raise RuntimeError("client disconnected")
        self.sent_events.append(event)


def test_successful_sink_records_benchmark_without_false_retry() -> None:
    attempts: list[list[object]] = []
    collector = BenchmarkingCollector()

    async def sink(batch: list[object]) -> int:
        attempts.append(batch)
        return len(batch)

    manager = ParsedLogBatchManager(
        batch_size=1,
        sink=sink,
        benchmarking_collector=collector,
    )

    asyncio.run(manager.add(object()))

    stats = manager.get_stats()
    assert len(attempts) == 1
    assert stats["flushed_batch_count"] == 1
    assert stats["failed_flush_attempt_count"] == 0
    assert stats["current_buffer_size"] == 0
    assert stats["last_sink_error"] is None
    assert collector.get_health_metrics()["db_batch_duration_ms"] >= 0.0


def test_benchmarking_record_dispatch_is_explicit() -> None:
    collector = BenchmarkingCollector()

    collector.record("sink_latency_ms", 12.5)
    assert collector.get_health_metrics()["db_batch_duration_ms"] == 12.5

    with pytest.raises(ValueError, match="Unsupported benchmarking metric"):
        collector.record("unclassified_value", 1)


def test_stream_sampler_exports_pending_lag_backlog_and_dlq() -> None:
    redis = FakeRedis(stream_length=18, dlq_size=3, pending=5, lag=7)
    sampler = StreamMetricsSampler()

    snapshot = asyncio.run(
        sampler.refresh(
            redis,
            stream_name="logs:stream",
            group_name="log_workers",
            min_interval_seconds=0,
        )
    )

    assert snapshot == get_stream_metrics_snapshot()
    assert snapshot["stream_length"] == 18
    assert snapshot["pending_entries"] == 5
    assert snapshot["lag"] == 7
    assert snapshot["backlog"] == 12
    assert snapshot["dlq_size"] == 3
    assert snapshot["lag_available"] is True
    assert snapshot["redis_available"] is True


def test_stream_sampler_falls_back_to_pending_when_lag_is_unavailable() -> None:
    redis = FakeRedis(stream_length=9, dlq_size=0, pending=4, lag=None)
    sampler = StreamMetricsSampler()

    snapshot = asyncio.run(
        sampler.refresh(
            redis,
            stream_name="logs:stream",
            group_name="log_workers",
            min_interval_seconds=0,
        )
    )

    assert snapshot["lag"] is None
    assert snapshot["lag_available"] is False
    assert snapshot["backlog"] == snapshot["pending_entries"] == 4


def test_stream_sampler_is_throttled_and_marks_redis_failure() -> None:
    redis = FakeRedis()
    sampler = StreamMetricsSampler()

    async def run() -> tuple[dict[str, Any], dict[str, Any]]:
        first = await sampler.refresh(
            redis,
            stream_name="logs:stream",
            group_name="log_workers",
            min_interval_seconds=60,
        )
        calls_after_first = len(redis.calls)
        second = await sampler.refresh(
            redis,
            stream_name="logs:stream",
            group_name="log_workers",
            min_interval_seconds=60,
        )
        assert len(redis.calls) == calls_after_first
        return first, second

    first, second = asyncio.run(run())
    assert first == second

    failed = asyncio.run(
        sampler.refresh(
            FailingRedis(),
            stream_name="logs:stream",
            group_name="log_workers",
            min_interval_seconds=0,
        )
    )
    assert failed["redis_available"] is False


def test_worker_and_drain3_snapshots_are_bounded_and_reusable() -> None:
    drain = record_drain_worker_stats(
        {
            "running": True,
            "processed_count": 12,
            "error_count": 2,
            "dlq_count": 1,
            "queue_size": 3,
            "last_processed_at": "2026-08-22T12:00:00+00:00",
        },
        parser_stats={"cluster_count": 8, "total_cluster_size": 42},
    )
    feature = record_feature_worker_stats(
        {
            "running": True,
            "features_extracted": 4,
            "extraction_errors": 1,
            "feature_buffer_size": 2,
            "last_extraction_at": "2026-08-22T12:00:01+00:00",
        }
    )

    workers = get_worker_metrics_snapshot()
    assert drain["running"] is True
    assert drain["processed"] == 12.0
    assert drain["heartbeat_timestamp_seconds"] is not None
    assert feature["processed"] == 4.0
    assert workers["drain"]["queue_size"] == 3.0
    assert workers["feature_extraction"]["errors"] == 1.0


def test_ml_status_distinguishes_missing_model_and_counts_inference(tmp_path: Path) -> None:
    before = get_ml_metrics_snapshot()
    missing = set_ml_status(loaded=False)
    assert missing["model_loaded"] is False
    assert missing["model_version"] == "unavailable"

    model_path = tmp_path / "temporary-test-model.joblib"
    model_path.write_bytes(b"test artifact")
    loaded = set_ml_status(
        loaded=True,
        model_version="isolation_forest_v1",
        model_path=model_path,
    )
    observe_ml_inference(success=True, is_anomaly=True)
    observe_ml_inference(success=False)

    after = get_ml_metrics_snapshot()
    assert loaded["model_loaded"] is True
    assert loaded["model_age_seconds"] >= 0.0
    assert after["inference_total"] == before["inference_total"] + 2
    assert after["inference_errors_total"] == before["inference_errors_total"] + 1
    assert after["anomalies_total"] == before["anomalies_total"] + 1


@pytest.mark.asyncio
async def test_broadcaster_hooks_count_handshake_and_delivery_outcomes() -> None:
    before = get_websocket_metrics_snapshot()
    manager = HighLoadBroadcaster(frame_rate_ms=1.0)
    healthy = FakeWebSocket()
    failed = FakeWebSocket(fail_send=True)

    manager.record_connection_attempt()
    manager.record_authentication_failure()
    await manager.connect(healthy)  # type: ignore[arg-type]
    await manager.connect(failed)  # type: ignore[arg-type]
    await manager.broadcast({"type": "log.parsed", "payload": {"service": "api"}})
    await asyncio.sleep(0.03)
    await manager.stop()

    after = get_websocket_metrics_snapshot()
    assert healthy.sent_events
    assert after["connection_attempts_total"] == before["connection_attempts_total"] + 1
    assert after["authentication_failures_total"] == before["authentication_failures_total"] + 1
    assert after["frames_sent_total"] >= before["frames_sent_total"] + 1
    assert after["send_failures_total"] >= before["send_failures_total"] + 1


def test_metrics_are_registered_in_the_default_prometheus_registry() -> None:
    update_stream_metrics(
        stream_length=1,
        pending_entries=2,
        lag=3,
        dlq_size=4,
    )
    payload = generate_latest().decode("utf-8")
    for metric_name in (
        "logsentinel_stream_length",
        "logsentinel_stream_pending_entries",
        "logsentinel_stream_backlog",
        "logsentinel_worker_running",
        "logsentinel_drain3_template_clusters",
        "logsentinel_ml_model_loaded",
        "logsentinel_websocket_frames_sent_total",
    ):
        assert metric_name in payload


def test_local_prometheus_scrape_config_targets_backend_service() -> None:
    config_path = Path(__file__).resolve().parents[2] / "deploy" / "monitoring" / "prometheus.yml"
    config = config_path.read_text(encoding="utf-8")
    assert "job_name: logsentinel-backend" in config
    assert "metrics_path: /metrics" in config
    assert "backend:8000" in config


def test_liveness_readiness_and_metrics_contract(monkeypatch) -> None:
    class DownRedis:
        async def ping(self) -> None:
            raise ConnectionError("down")

    monkeypatch.setattr(main_module.app.state, "redis", DownRedis(), raising=False)
    monkeypatch.setattr(main_module, "get_engine", lambda: (_ for _ in ()).throw(RuntimeError("db down")))

    client = TestClient(main_module.app)
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/live").json()["status"] == "ok"
    readiness = client.get("/ready")
    assert readiness.status_code == 503
    assert readiness.json()["status"] == "not_ready"
    assert client.get("/metrics").status_code == 200
