import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.models import ParsedLog
from backend.app.services.batch_manager import ParsedLogBatchManager


def parsed_log(index: int) -> ParsedLog:
    timestamp = datetime(2026, 7, 22, tzinfo=timezone.utc) + timedelta(seconds=index)
    return ParsedLog(
        timestamp=timestamp,
        service=f"service-{index}",
        level="info",
        raw_message=f"log {index}",
        template_id=str(index),
        template_text="log <*>",
        parameters=[{"value": str(index), "mask_name": "NUM"}],
        metadata={"sequence": index},
        parsed_at=timestamp,
    )


def pending_ids(manager: ParsedLogBatchManager) -> list[str]:
    return [record.template_id for record in manager.get_pending_records()]


def batch_ids(batch: list[ParsedLog]) -> list[str]:
    return [record.template_id for record in batch]


def test_records_below_threshold_remain_pending() -> None:
    manager = ParsedLogBatchManager(batch_size=3)

    async def run() -> None:
        await manager.add(parsed_log(1))
        await manager.add(parsed_log(2))

    asyncio.run(run())

    assert pending_ids(manager) == ["1", "2"]
    assert manager.get_stats()["flushed_batch_count"] == 0


def test_threshold_flush_invokes_async_sink_once() -> None:
    received: list[list[ParsedLog]] = []

    async def sink(batch: list[ParsedLog]) -> int:
        received.append(batch)
        return len(batch)

    manager = ParsedLogBatchManager(batch_size=2, sink=sink)

    async def run() -> None:
        await manager.add(parsed_log(1))
        await manager.add(parsed_log(2))

    asyncio.run(run())

    assert [batch_ids(batch) for batch in received] == [["1", "2"]]
    assert manager.get_stats()["current_buffer_size"] == 0
    assert manager.get_stats()["flushed_batch_count"] == 1
    assert manager.get_stats()["flushed_record_count"] == 2
    assert manager.get_stats()["last_sink_result"] == 2
    assert all(isinstance(record, ParsedLog) for record in received[0])


def test_threshold_flush_without_sink_keeps_debug_batch_compatibility() -> None:
    manager = ParsedLogBatchManager(batch_size=2)

    async def run() -> None:
        await manager.add(parsed_log(1))
        await manager.add(parsed_log(2))

    asyncio.run(run())

    assert [batch_ids(batch) for batch in manager.get_flushed_batches()] == [
        ["1", "2"]
    ]
    assert manager.get_stats()["current_buffer_size"] == 0


def test_manual_flush_sends_records_and_empty_flush_does_not_call_sink() -> None:
    received: list[list[ParsedLog]] = []

    async def sink(batch: list[ParsedLog]) -> int:
        received.append(batch)
        return len(batch)

    manager = ParsedLogBatchManager(batch_size=10, sink=sink)

    async def run() -> None:
        assert await manager.flush() is False
        await manager.add(parsed_log(1))
        await manager.add(parsed_log(2))
        assert await manager.flush() is True
        assert await manager.flush() is False

    asyncio.run(run())

    assert [batch_ids(batch) for batch in received] == [["1", "2"]]
    assert manager.get_stats()["current_buffer_size"] == 0


def test_periodic_flush_sends_pending_records_and_stops_cleanly() -> None:
    received: list[list[ParsedLog]] = []
    sink_called = asyncio.Event()

    async def sink(batch: list[ParsedLog]) -> int:
        received.append(batch)
        sink_called.set()
        return len(batch)

    manager = ParsedLogBatchManager(
        batch_size=10,
        flush_interval_seconds=0.001,
        sink=sink,
    )

    async def run() -> None:
        await manager.add(parsed_log(1))
        manager.start_periodic_flush()
        await asyncio.wait_for(sink_called.wait(), timeout=1.0)
        await manager.stop_periodic_flush()

    asyncio.run(run())

    assert [batch_ids(batch) for batch in received] == [["1"]]
    stats = manager.get_stats()
    assert stats["periodic_flush_enabled"] is False
    assert stats["periodic_flush_count"] == 1
    assert stats["current_buffer_size"] == 0


def test_synchronous_sink_compatibility_is_preserved() -> None:
    received: list[list[ParsedLog]] = []

    def sink(batch: list[ParsedLog]) -> int:
        received.append(batch)
        return len(batch)

    manager = ParsedLogBatchManager(batch_size=1, sink=sink)
    asyncio.run(manager.add(parsed_log(1)))

    assert [batch_ids(batch) for batch in received] == [["1"]]
    assert manager.get_stats()["last_sink_result"] == 1


def test_overlapping_threshold_flushes_serialize_sink_invocations() -> None:
    active_invocations = 0
    maximum_concurrent_invocations = 0
    received: list[list[ParsedLog]] = []
    first_sink_entered = asyncio.Event()
    release_first_sink = asyncio.Event()

    async def sink(batch: list[ParsedLog]) -> int:
        nonlocal active_invocations, maximum_concurrent_invocations
        active_invocations += 1
        maximum_concurrent_invocations = max(
            maximum_concurrent_invocations,
            active_invocations,
        )
        try:
            if not received:
                first_sink_entered.set()
                await release_first_sink.wait()
            received.append(batch)
            return len(batch)
        finally:
            active_invocations -= 1

    manager = ParsedLogBatchManager(batch_size=1, sink=sink)

    async def run() -> None:
        first_add = asyncio.create_task(manager.add(parsed_log(1)))
        await asyncio.wait_for(first_sink_entered.wait(), timeout=1.0)

        second_add = asyncio.create_task(manager.add(parsed_log(2)))
        await asyncio.sleep(0)
        assert pending_ids(manager) == ["2"]
        assert manager.get_stats()["flush_in_progress"] is True

        release_first_sink.set()
        await asyncio.gather(first_add, second_add)

    asyncio.run(run())

    assert maximum_concurrent_invocations == 1
    assert [batch_ids(batch) for batch in received] == [["1"], ["2"]]
    assert manager.get_stats()["flushed_batch_count"] == 2


def test_failed_flush_restores_records_in_original_order() -> None:
    async def sink(_: list[ParsedLog]) -> int:
        raise RuntimeError("database unavailable")

    manager = ParsedLogBatchManager(batch_size=10, sink=sink)

    async def run() -> None:
        await manager.add(parsed_log(1))
        await manager.add(parsed_log(2))
        assert await manager.flush() is False

    asyncio.run(run())

    stats = manager.get_stats()
    assert pending_ids(manager) == ["1", "2"]
    assert stats["current_buffer_size"] == 2
    assert stats["flushed_batch_count"] == 0
    assert stats["flushed_record_count"] == 0
    assert stats["failed_flush_attempt_count"] == 1
    assert stats["failed_batch_count"] == 1
    assert stats["last_sink_error"] == "RuntimeError: database unavailable"
    assert [batch_ids(batch) for batch in manager.get_failed_batches()] == [["1", "2"]]


def test_later_flush_retries_restored_records_without_loss() -> None:
    attempts: list[list[ParsedLog]] = []
    successful_batches: list[list[ParsedLog]] = []
    first = parsed_log(1)
    second = parsed_log(2)

    async def sink(batch: list[ParsedLog]) -> int:
        attempts.append(batch)
        if len(attempts) == 1:
            raise RuntimeError("temporary outage")
        successful_batches.append(batch)
        return len(batch)

    manager = ParsedLogBatchManager(batch_size=10, sink=sink)

    async def run() -> None:
        await manager.add(first)
        await manager.add(second)
        assert await manager.flush() is False
        assert pending_ids(manager) == ["1", "2"]
        assert await manager.flush() is True

    asyncio.run(run())

    assert [batch_ids(batch) for batch in attempts] == [["1", "2"], ["1", "2"]]
    assert [batch_ids(batch) for batch in successful_batches] == [["1", "2"]]
    assert attempts[0][0] is first
    assert attempts[0][1] is second
    assert attempts[1][0] is first
    assert attempts[1][1] is second
    assert pending_ids(manager) == []
    stats = manager.get_stats()
    assert stats["failed_flush_attempt_count"] == 1
    assert stats["flushed_batch_count"] == 1
    assert stats["flushed_record_count"] == 2
    assert stats["last_sink_error"] is None


def test_records_added_during_failed_flush_follow_restored_records() -> None:
    sink_entered = asyncio.Event()
    release_sink = asyncio.Event()

    async def sink(_: list[ParsedLog]) -> int:
        sink_entered.set()
        await release_sink.wait()
        raise RuntimeError("write failed")

    manager = ParsedLogBatchManager(batch_size=10, sink=sink)

    async def run() -> None:
        await manager.add(parsed_log(1))
        await manager.add(parsed_log(2))
        flush_task = asyncio.create_task(manager.flush())
        await asyncio.wait_for(sink_entered.wait(), timeout=1.0)

        await manager.add(parsed_log(3))
        await manager.add(parsed_log(4))
        assert pending_ids(manager) == ["3", "4"]

        release_sink.set()
        assert await flush_task is False

    asyncio.run(run())

    assert pending_ids(manager) == ["1", "2", "3", "4"]
    assert manager.get_stats()["current_buffer_size"] == 4


def test_cancellation_restores_records_and_propagates_cancelled_error() -> None:
    sink_entered = asyncio.Event()
    wait_forever = asyncio.Event()

    async def sink(_: list[ParsedLog]) -> int:
        sink_entered.set()
        await wait_forever.wait()
        return 1

    manager = ParsedLogBatchManager(batch_size=10, sink=sink)

    async def run() -> None:
        await manager.add(parsed_log(1))
        await manager.add(parsed_log(2))
        flush_task = asyncio.create_task(manager.flush())
        await asyncio.wait_for(sink_entered.wait(), timeout=1.0)

        flush_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await flush_task

    asyncio.run(run())

    stats = manager.get_stats()
    assert pending_ids(manager) == ["1", "2"]
    assert stats["current_buffer_size"] == 2
    assert stats["flushed_batch_count"] == 0
    assert stats["failed_flush_attempt_count"] == 1
    assert stats["cancelled_flush_attempt_count"] == 1
    assert stats["flush_in_progress"] is False


def test_shutdown_flush_persists_pending_records() -> None:
    received: list[list[ParsedLog]] = []

    async def sink(batch: list[ParsedLog]) -> int:
        received.append(batch)
        return len(batch)

    manager = ParsedLogBatchManager(batch_size=10, sink=sink)

    async def run() -> None:
        await manager.add(parsed_log(1))
        await manager.shutdown_flush()

    asyncio.run(run())

    assert [batch_ids(batch) for batch in received] == [["1"]]
    stats = manager.get_stats()
    assert stats["shutdown_flush_count"] == 1
    assert stats["flushed_batch_count"] == 1
    assert stats["current_buffer_size"] == 0


def test_failed_shutdown_flush_leaves_records_pending() -> None:
    async def sink(_: list[ParsedLog]) -> int:
        raise RuntimeError("shutdown database unavailable")

    manager = ParsedLogBatchManager(batch_size=10, sink=sink)

    async def run() -> None:
        await manager.add(parsed_log(1))
        await manager.shutdown_flush()

    asyncio.run(run())

    stats = manager.get_stats()
    assert pending_ids(manager) == ["1"]
    assert stats["shutdown_flush_count"] == 0
    assert stats["flushed_batch_count"] == 0
    assert stats["failed_flush_attempt_count"] == 1
    assert stats["current_buffer_size"] == 1


def test_failed_periodic_attempt_is_not_counted_as_success() -> None:
    sink_attempted = asyncio.Event()
    release_sink = asyncio.Event()

    async def sink(_: list[ParsedLog]) -> int:
        sink_attempted.set()
        await release_sink.wait()
        raise RuntimeError("periodic database unavailable")

    manager = ParsedLogBatchManager(
        batch_size=10,
        flush_interval_seconds=0.001,
        sink=sink,
    )

    async def run() -> None:
        await manager.add(parsed_log(1))
        manager.start_periodic_flush()
        await asyncio.wait_for(sink_attempted.wait(), timeout=1.0)
        stop_task = asyncio.create_task(manager.stop_periodic_flush())
        await asyncio.sleep(0)
        release_sink.set()
        await stop_task

    asyncio.run(run())

    stats = manager.get_stats()
    assert stats["periodic_flush_count"] == 0
    assert stats["flushed_batch_count"] == 0
    assert stats["failed_flush_attempt_count"] == 1
    assert stats["current_buffer_size"] == 1


def test_error_summary_redacts_connection_credentials() -> None:
    async def sink(_: list[ParsedLog]) -> int:
        raise RuntimeError("postgresql://user:super-secret@database/logs")

    manager = ParsedLogBatchManager(batch_size=1, sink=sink)
    asyncio.run(manager.add(parsed_log(1)))

    error = manager.get_stats()["last_sink_error"]
    assert error == "RuntimeError: postgresql://<redacted>@database/logs"
    assert "super-secret" not in error
