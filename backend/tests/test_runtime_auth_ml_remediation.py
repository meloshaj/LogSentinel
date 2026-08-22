"""Focused regression coverage for the Runtime/Auth/ML remediation slice."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from drain3.file_persistence import FilePersistence

import backend.app.workers.drain_worker as drain_worker_module
from backend.app.ml.anomaly_detector import (
    CANONICAL_MODEL_FILENAME,
    IsolationForestAnomalyDetector,
)
from backend.app.models import FeatureVector, ParsedLog
from backend.app.services.batch_manager import ParsedLogBatchManager
from backend.app.services.drain_parser import (
    DrainParser,
    get_drain3_redis_settings,
    get_drain3_state_path,
)
from backend.app.workers.drain_worker import DrainWorker, StreamMessageOutcome
from backend.app.workers.feature_worker import FeatureExtractionWorker
from backend.app.workers.stream_cleaner import StreamCleanerWorker


class FailingParser:
    def __init__(self) -> None:
        self._miner = MagicMock()

    def parse(self, raw_message: str, metadata: dict | None = None) -> ParsedLog:
        raise ValueError("parser failure")


class SuccessfulParser:
    def __init__(self) -> None:
        self._miner = MagicMock()

    def parse(self, raw_message: str, metadata: dict | None = None) -> ParsedLog:
        now = datetime.now(timezone.utc)
        return ParsedLog(
            id="01J00000000000000000000000",
            timestamp=now,
            service="api",
            level="info",
            raw_message=raw_message,
            template_id="template-1",
            template_text="message <*> ",
            parsed_at=now,
        )


def make_redis(*, retry_counts: list[int] | None = None) -> MagicMock:
    redis = MagicMock()
    redis.incr = AsyncMock(side_effect=retry_counts or [])
    redis.expire = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    redis.xadd = AsyncMock(return_value="dlq-1")
    redis.xack = AsyncMock(return_value=1)
    return redis


def stream_entry() -> dict[str, str]:
    return {
        "payload": json.dumps(
            {"logs": [{"service_name": "api", "message": "request failed"}]}
        )
    }


def test_drain_state_path_is_configurable_and_not_filesystem_root(tmp_path, monkeypatch) -> None:
    configured = tmp_path / "drain" / "state.bin"
    monkeypatch.setenv("DRAIN3_STATE_PATH", str(configured))

    assert get_drain3_state_path() == configured
    parser = DrainParser(
        state_path=str(configured),
        persistence=FilePersistence(str(configured)),
    )
    assert parser.state_path == configured
    assert configured.parent.exists()


def test_drain3_redis_state_uses_the_shared_url_contract(monkeypatch) -> None:
    monkeypatch.setenv("REDIS_URL", "rediss://:secret%20value@valkey.example:6380/4")

    settings = get_drain3_redis_settings()

    assert settings == {
        "redis_host": "valkey.example",
        "redis_port": 6380,
        "redis_db": 4,
        "redis_pass": "secret value",
        "is_ssl": True,
    }


@pytest.mark.asyncio
async def test_stream_parser_failure_remains_pending_then_dlqs_before_ack() -> None:
    worker = DrainWorker(None, FailingParser(), batch_manager=ParsedLogBatchManager())  # type: ignore[arg-type]
    redis = make_redis(retry_counts=[1, 2, 3])
    worker.set_redis_client(redis)

    first = await worker._process_stream_message("100-0", stream_entry())
    second = await worker._process_stream_message("100-0", stream_entry())
    terminal = await worker._process_stream_message("100-0", stream_entry())

    assert first is StreamMessageOutcome.RETRYABLE_FAILURE
    assert second is StreamMessageOutcome.RETRYABLE_FAILURE
    assert terminal is StreamMessageOutcome.TERMINALLY_ROUTED_TO_DLQ
    assert redis.xadd.call_count == 1
    assert redis.xack.call_count == 1


@pytest.mark.asyncio
async def test_stream_ack_waits_for_successful_persistence() -> None:
    persisted = False

    async def sink(batch: list[ParsedLog]) -> int:
        nonlocal persisted
        persisted = True
        return len(batch)

    manager = ParsedLogBatchManager(batch_size=500, sink=sink)
    worker = DrainWorker(None, SuccessfulParser(), batch_manager=manager)  # type: ignore[arg-type]
    redis = make_redis()

    async def assert_persisted_before_ack(*args) -> int:
        assert persisted
        return 1

    redis.xack = AsyncMock(side_effect=assert_persisted_before_ack)
    worker.set_redis_client(redis)

    outcome = await worker._process_stream_message("101-0", stream_entry())

    assert outcome is StreamMessageOutcome.SUCCESSFULLY_PROCESSED
    assert persisted
    redis.xack.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_cleaner_never_claims_or_acks_pending_work() -> None:
    cleaner = StreamCleanerWorker()
    redis = MagicMock()
    redis.xautoclaim = AsyncMock()
    redis.xack = AsyncMock()
    cleaner.set_redis_client(redis)

    await cleaner._clean_orphans()

    redis.xautoclaim.assert_not_awaited()
    redis.xack.assert_not_awaited()


@pytest.mark.asyncio
async def test_drain_recovery_routes_stale_pending_entries_through_normal_processing(
    monkeypatch,
) -> None:
    worker = DrainWorker(None, SuccessfulParser(), batch_manager=ParsedLogBatchManager())  # type: ignore[arg-type]
    worker._running = True
    redis = make_redis()
    redis.xautoclaim = AsyncMock(
        return_value=(
            "0-0",
            [("102-0", stream_entry())],
            [],
        )
    )
    worker.set_redis_client(redis)

    async def stop_after_processing(*args, **kwargs):
        worker._running = False
        return StreamMessageOutcome.SUCCESSFULLY_PROCESSED

    worker._process_stream_message = AsyncMock(side_effect=stop_after_processing)  # type: ignore[method-assign]
    monkeypatch.setattr(drain_worker_module.asyncio, "sleep", AsyncMock())

    await worker.recover_pending_messages()

    redis.xautoclaim.assert_awaited_once()
    worker._process_stream_message.assert_awaited_once_with("102-0", stream_entry())
    redis.xack.assert_not_awaited()


def feature_vector(index: int) -> FeatureVector:
    now = datetime.now(timezone.utc)
    return FeatureVector(
        window_id=f"window-{index}",
        timestamp=now,
        window_start=now,
        window_end=now,
        log_count=10 + index,
        unique_templates=2,
        error_count=index % 2,
        warning_count=0,
        template_frequencies={"template-1": 1.0},
        service_distribution={"api": 10 + index},
        logs_per_second=1.0,
        features={
            "log_count": float(10 + index),
            "info_count": float(9 + index),
            "warning_count": 0.0,
            "error_count": float(index % 2),
            "error_ratio": 0.05,
            "active_services": 1.0,
            "unique_templates": 2.0,
            "dominant_service_count": float(10 + index),
            "dominant_template_count": 10.0,
            "logs_per_second": 1.0,
            "avg_logs_per_minute": 60.0,
            "burst_indicator": 0.0,
        },
    )


def test_canonical_joblib_artifact_round_trip_and_health(tmp_path) -> None:
    assert CANONICAL_MODEL_FILENAME == "isolation_forest.joblib"
    detector = IsolationForestAnomalyDetector(random_state=42, contamination=0.2)
    detector.train([feature_vector(index) for index in range(6)])
    artifact = tmp_path / CANONICAL_MODEL_FILENAME
    detector.save_model(artifact)

    loaded = IsolationForestAnomalyDetector.load_model(artifact)
    prediction = loaded.predict(feature_vector(0))
    health = loaded.get_health()

    assert prediction["model_version"] == "isolation_forest_v1"
    assert health["model_loaded"] is True
    assert health["artifact_path"] == str(artifact)
    assert health["inference_total"] == 1


def test_missing_model_is_operator_visible() -> None:
    worker = FeatureExtractionWorker(
        anomaly_model_path=Path("missing") / CANONICAL_MODEL_FILENAME,
    )

    health = worker.get_model_health()

    assert health["model_loaded"] is False
    assert health["artifact_path"].endswith(CANONICAL_MODEL_FILENAME)
