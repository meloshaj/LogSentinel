"""Focused regression coverage for Remediation 05B.

The PostgreSQL/Valkey cases are opt-in because they require a disposable
service environment.  The default tests exercise the ordering and identity
contracts without replacing the real service tests with mocks.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from backend.app.core.pipeline_identity import (
    feature_contribution_key,
    feature_window_id,
)
from backend.app.ml.feature_extractor import SlidingWindowFeatureExtractor, WindowConfig
from backend.app.models import FeatureVector, ParsedLog, PerformanceEvent
from backend.app.observability.metrics import EVENT_QUEUE_DROPS_TOTAL
from backend.app.repositories.log_repository import (
    LogRepository,
    PersistResult,
    PersistResults,
    PersistStatus,
)
from backend.app.services.batch_manager import ParsedLogBatchManager
from backend.app.services.durable_queue import DurableQueue
from backend.app.workers.drain_worker import DrainWorker
from backend.app.workers.event_manager import EventManager
from backend.app.workers.feature_worker import FeatureExtractionWorker

ROOT = Path(__file__).resolve().parents[2]


def _parsed(
    *,
    tenant_id: str = "tenant-a",
    owner_user_id: int = 101,
    event_id: str = "event-a",
    timestamp=None,
) -> ParsedLog:
    timestamp = timestamp or datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    return ParsedLog(
        id=f"row-{tenant_id}-{event_id}",
        event_id=event_id,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        timestamp=timestamp,
        service="api",
        level="info",
        raw_message="request completed",
        template_id="template-1",
        template_text="request completed",
        parameters=[],
        metadata={},
        parsed_at=timestamp,
    )


def _feature(
    *, tenant_id: str = "tenant-a", window_id: str = "window-a"
) -> FeatureVector:
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    return FeatureVector(
        window_id=window_id,
        tenant_id=tenant_id,
        owner_user_id=101,
        timestamp=now,
        window_start=now,
        window_end=now + timedelta(seconds=10),
        log_count=1,
        unique_templates=1,
        error_count=0,
        warning_count=0,
        template_frequencies={"template-1": 1.0},
        template_entropy=0.0,
        service_distribution={"api": 1},
        logs_per_second=0.1,
        feature_array=[0.0] * 12,
        feature_names=[f"feature-{i}" for i in range(12)],
        features={},
    )


class _OrderingParser:
    def __init__(self, parsed: ParsedLog) -> None:
        self.parsed = parsed

    def parse(self, raw_message: str, metadata=None, **kwargs) -> ParsedLog:
        return self.parsed


class _TypedPersistenceBatch(ParsedLogBatchManager):
    def __init__(self, results: list[PersistResults], order: list[str]) -> None:
        super().__init__(batch_size=1000, flush_interval_seconds=60.0)
        self.results = results
        self.order = order

    async def persist_batch(self, batch: list[ParsedLog]):
        self.order.append("raw-commit")
        return True, self.results.pop(0)


def test_raw_acceptance_result_is_typed_and_tenant_qualified() -> None:
    result = PersistResults(
        [
            PersistResult("tenant-a", "same-id", PersistStatus.NEWLY_INSERTED),
            PersistResult("tenant-a", "same-id", PersistStatus.ALREADY_PROCESSED),
            PersistResult("tenant-b", "same-id", PersistStatus.NEWLY_INSERTED),
        ]
    )

    assert [item.status for item in result] == [
        PersistStatus.NEWLY_INSERTED,
        PersistStatus.ALREADY_PROCESSED,
        PersistStatus.NEWLY_INSERTED,
    ]
    assert result.newly_inserted_count == 2
    assert result.replay_count == 1
    assert PersistStatus.FAILED.value == "failed"


def test_raw_repository_reports_failed_events_without_ackable_success() -> None:
    class FailingEngine:
        def connect(self):
            raise OSError("database unavailable")

    result = asyncio.run(
        LogRepository(engine=FailingEngine()).bulk_insert_parsed_logs([_parsed()])  # type: ignore[arg-type]
    )

    assert result.failed_count == 1
    assert result[0].status is PersistStatus.FAILED


def test_stream_callback_waits_for_raw_commit_and_replay_does_not_callback() -> None:
    async def run() -> tuple[list[str], list[ParsedLog]]:
        parsed = _parsed()
        order: list[str] = []
        callback_logs: list[ParsedLog] = []
        batch = _TypedPersistenceBatch(
            [
                PersistResults(
                    [PersistResult("tenant-a", "event-a", PersistStatus.NEWLY_INSERTED)]
                ),
                PersistResults(
                    [
                        PersistResult(
                            "tenant-a", "event-a", PersistStatus.ALREADY_PROCESSED
                        )
                    ]
                ),
            ],
            order,
        )
        worker = DrainWorker(
            None,
            _OrderingParser(parsed),  # type: ignore[arg-type]
            batch_manager=batch,
            on_log_parsed=lambda value: (
                order.append("feature-callback"),
                callback_logs.append(value),
            ),
        )

        first = await worker.process_one(
            {"logs": [{"message": parsed.raw_message}]},
            message_id="1-0",
            _persist_before_ack=True,
            trusted_tenant_id="tenant-a",
        )
        second = await worker.process_one(
            {"logs": [{"message": parsed.raw_message}]},
            message_id="2-0",
            _persist_before_ack=True,
            trusted_tenant_id="tenant-a",
        )
        assert first and second
        return order, callback_logs

    order, callback_logs = asyncio.run(run())
    assert order == ["raw-commit", "feature-callback", "raw-commit"]
    assert len(callback_logs) == 1


def test_same_batch_duplicate_is_one_new_acceptance() -> None:
    results = PersistResults()
    seen: set[tuple[str, str]] = set()
    for item in (("tenant-a", "same-id"), ("tenant-a", "same-id")):
        status = (
            PersistStatus.NEWLY_INSERTED
            if item not in seen
            else PersistStatus.ALREADY_PROCESSED
        )
        seen.add(item)
        results.append(PersistResult(*item, status))

    assert results.newly_inserted_count == 1
    assert results.replay_count == 1


def test_feature_contribution_and_window_identities_are_stable_and_tenant_scoped() -> (
    None
):
    start = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    end = start + timedelta(seconds=10)
    assert feature_contribution_key("tenant-a", "event-1") == feature_contribution_key(
        "tenant-a", "event-1"
    )
    assert feature_contribution_key("tenant-a", "event-1") != feature_contribution_key(
        "tenant-b", "event-1"
    )
    assert feature_window_id("tenant-a", None, start, end) == feature_window_id(
        "tenant-a", None, start, end
    )
    assert feature_window_id("tenant-a", None, start, end) != feature_window_id(
        "tenant-b", None, start, end
    )


def test_extractor_deduplicates_source_event_and_regenerates_same_window() -> None:
    config = WindowConfig(
        window_size_seconds=10, stride_seconds=5, min_logs_per_window=1
    )
    log = _parsed(timestamp=datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc))
    first = SlidingWindowFeatureExtractor(config)
    second = SlidingWindowFeatureExtractor(config)
    first.add_log(log)
    first.add_log(log.model_copy())
    second.add_log(log)

    windows_one = first.peek_pending_windows(log.timestamp + timedelta(seconds=11))
    windows_two = second.peek_pending_windows(log.timestamp + timedelta(seconds=11))

    assert len(windows_one) == 1
    assert len(windows_one[0].logs) == 1
    assert windows_one[0].window_id == windows_two[0].window_id
    first.commit_pending_windows(windows_one)
    assert first.peek_pending_windows(log.timestamp + timedelta(seconds=11)) == []


class _FeatureRegistrationFailure:
    _engine = None

    async def get_recent_feature_inputs(self, *, since, limit):
        return []

    async def prune_feature_inputs(self, *, before):
        return 0

    async def register_feature_work(self, feature_vectors):
        raise OSError("feature database unavailable")


def test_feature_registration_failure_does_not_advance_window_cursor() -> None:
    async def run() -> FeatureExtractionWorker:
        worker = FeatureExtractionWorker(
            window_config=WindowConfig(
                window_size_seconds=10, stride_seconds=5, min_logs_per_window=1
            ),
            feature_repository=_FeatureRegistrationFailure(),  # type: ignore[arg-type]
        )
        worker.process_durable_work = AsyncMock(return_value=0)  # type: ignore[method-assign]
        log = _parsed(timestamp=datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc))
        worker.add_parsed_log(log)
        assert (
            await worker.extract_pending_features(log.timestamp + timedelta(seconds=11))
            == []
        )
        return worker

    worker = asyncio.run(run())
    state = worker._extractors[("tenant-a", 101)].get_stats()
    assert state["last_window_end"] is None
    assert state["current_buffer_size"] == 1


def test_event_queue_contract_separates_critical_and_noncritical() -> None:
    manager = EventManager(max_queue_size=1)
    manager.queue.put_nowait(
        PerformanceEvent(metric_name="first", current_value=2, threshold=1)
    )

    assert (
        manager.enqueue_performance_event(
            PerformanceEvent(metric_name="second", current_value=2, threshold=1)
        )
        is False
    )
    assert manager.get_stats()["noncritical_queue_drops"] == 1
    assert manager.get_stats()["dlq_count"] == 0
    assert EVENT_QUEUE_DROPS_TOTAL.labels(event_class="noncritical")._value.get() >= 1

    with pytest.raises(RuntimeError, match="durable feature stage"):
        manager.enqueue_feature_vector(_feature())
    assert manager.queue.qsize() == 1
    assert manager.get_stats()["critical_queue_rejections"] == 1


def test_durable_feature_queue_uses_completed_failed_states_and_lease_fields() -> None:
    queue = DurableQueue(
        engine=SimpleNamespace(),
        success_status="completed",
        terminal_status="failed",
    )
    assert queue.success_status == "completed"
    assert queue.terminal_status == "failed"
    outbox_orm = (ROOT / "backend/app/core/pipeline_orm.py").read_text(encoding="utf-8")
    assert "lease_expires_at" in outbox_orm
    assert "last_error_category" in outbox_orm


def test_05b_schema_and_migration_contracts_are_additive() -> None:
    init_sql = (ROOT / "scripts/init.sql").read_text(encoding="utf-8")
    migration = (
        ROOT / "scripts/migrations/20260912_0009_pipeline_feature_durability.sql"
    ).read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS pipeline_feature_inputs" in init_sql
    assert "PRIMARY KEY (tenant_id, owner_user_id, event_id)" in init_sql
    assert "CREATE TABLE IF NOT EXISTS pipeline_feature_inputs" in migration
    assert "DROP TABLE" not in migration.upper()
    assert "DROP COLUMN" not in migration.upper()


def _integration_enabled() -> bool:
    return os.getenv("LOGSENTINEL_RUN_DISTRIBUTED_INTEGRATION") == "1"


@pytest.mark.asyncio
async def test_feature_outbox_retries_after_handler_failure_and_completes_once() -> (
    None
):
    """Opt-in real PostgreSQL lease/retry test; never replaced by a fake DB."""
    if not _integration_enabled():
        pytest.skip(
            "INTEGRATION VERIFICATION REQUIRED: set LOGSENTINEL_RUN_DISTRIBUTED_INTEGRATION=1"
        )

    from backend.app.core.pipeline_orm import outbox
    from sqlalchemy import delete, insert, select, update
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(os.environ["DATABASE_URL"])
    row_id = "rem05b-feature-retry"
    calls = 0
    fail_once = True

    async def handler(row, conn):
        nonlocal calls, fail_once
        calls += 1
        if fail_once:
            fail_once = False
            raise OSError("simulated database-stage outage")

    try:
        async with engine.begin() as conn:
            await conn.execute(delete(outbox).where(outbox.c.id == row_id))
            await conn.execute(
                insert(outbox).values(
                    id=row_id,
                    tenant_id="tenant-a",
                    topic="feature_window",
                    dedup_key=row_id,
                    payload={"window_id": row_id},
                    available_at=datetime.now(timezone.utc),
                )
            )
        queue = DurableQueue(
            engine=engine, success_status="completed", terminal_status="failed"
        )
        assert await queue.process_one({"feature_window": handler}) is True
        async with engine.begin() as conn:
            await conn.execute(
                update(outbox)
                .where(outbox.c.id == row_id)
                .values(available_at=datetime.now(timezone.utc))
            )
        assert await queue.process_one({"feature_window": handler}) is True
        async with engine.connect() as conn:
            state = (
                await conn.execute(select(outbox.c.status).where(outbox.c.id == row_id))
            ).scalar_one()
        assert state == "completed"
        assert calls == 2
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete(outbox).where(outbox.c.id == row_id))
        await engine.dispose()
