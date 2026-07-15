import asyncio

from backend.app.services.batch_manager import ParsedLogBatchManager


def parsed_log(index: int) -> dict:
    return {
        "raw_message": f"log {index}",
        "template_id": str(index),
        "template_text": "log <*>",
    }


def test_adding_499_logs_does_not_flush() -> None:
    manager = ParsedLogBatchManager(batch_size=500)

    async def run() -> None:
        for index in range(499):
            await manager.add(parsed_log(index))

    asyncio.run(run())

    stats = manager.get_stats()
    assert stats["current_buffer_size"] == 499
    assert stats["flushed_batch_count"] == 0
    assert stats["flushed_record_count"] == 0


def test_adding_500th_log_triggers_one_flush() -> None:
    manager = ParsedLogBatchManager(batch_size=500)

    async def run() -> None:
        for index in range(500):
            await manager.add(parsed_log(index))

    asyncio.run(run())

    stats = manager.get_stats()
    assert stats["current_buffer_size"] == 0
    assert stats["flushed_batch_count"] == 1
    assert stats["flushed_record_count"] == 500
    assert len(manager.get_flushed_batches()) == 1
    assert len(manager.get_flushed_batches()[0]) == 500


def test_manual_flush_flushes_remaining_logs() -> None:
    manager = ParsedLogBatchManager(batch_size=500)

    async def run() -> None:
        await manager.add(parsed_log(1))
        await manager.add(parsed_log(2))
        await manager.flush()

    asyncio.run(run())

    stats = manager.get_stats()
    assert stats["current_buffer_size"] == 0
    assert stats["flushed_batch_count"] == 1
    assert stats["flushed_record_count"] == 2
    assert stats["last_flush_at"] is not None


def test_sink_is_called_if_provided() -> None:
    received_batches: list[list[dict]] = []

    def sink(batch: list[dict]) -> None:
        received_batches.append(batch)

    manager = ParsedLogBatchManager(batch_size=2, sink=sink)

    async def run() -> None:
        await manager.add(parsed_log(1))
        await manager.add(parsed_log(2))

    asyncio.run(run())

    stats = manager.get_stats()
    assert stats["sink_configured"] is True
    assert stats["flushed_batch_count"] == 1
    assert stats["flushed_record_count"] == 2
    assert len(received_batches) == 1
    assert manager.get_flushed_batches() == []


def test_async_sink_result_is_stored() -> None:
    async def sink(batch: list[dict]) -> int:
        return len(batch)

    manager = ParsedLogBatchManager(batch_size=2, sink=sink)

    async def run() -> None:
        await manager.add(parsed_log(1))
        await manager.add(parsed_log(2))

    asyncio.run(run())

    stats = manager.get_stats()
    assert stats["flushed_batch_count"] == 1
    assert stats["flushed_record_count"] == 2
    assert stats["last_flush_record_count"] == 2
    assert stats["last_sink_result"] == 2
    assert stats["last_sink_error"] is None


def test_sink_error_does_not_crash_and_retains_failed_batch() -> None:
    async def sink(_: list[dict]) -> int:
        raise RuntimeError("database unavailable")

    manager = ParsedLogBatchManager(batch_size=2, sink=sink)

    async def run() -> None:
        await manager.add(parsed_log(1))
        await manager.add(parsed_log(2))

    asyncio.run(run())

    stats = manager.get_stats()
    assert stats["current_buffer_size"] == 0
    assert stats["flushed_batch_count"] == 0
    assert stats["flushed_record_count"] == 0
    assert stats["last_sink_result"] is None
    assert "RuntimeError: database unavailable" == stats["last_sink_error"]
    assert len(manager.get_failed_batches()) == 1
    assert len(manager.get_failed_batches()[0]) == 2


def test_periodic_flush_flushes_records_below_batch_size() -> None:
    manager = ParsedLogBatchManager(batch_size=500, flush_interval_seconds=0.01)

    async def run() -> None:
        await manager.add(parsed_log(1))
        manager.start_periodic_flush()
        await asyncio.sleep(0.03)
        await manager.stop_periodic_flush()

    asyncio.run(run())

    stats = manager.get_stats()
    assert stats["current_buffer_size"] == 0
    assert stats["flushed_batch_count"] == 1
    assert stats["flushed_record_count"] == 1
    assert stats["periodic_flush_count"] == 1


def test_periodic_flush_does_not_flush_empty_buffer() -> None:
    manager = ParsedLogBatchManager(batch_size=500, flush_interval_seconds=0.01)

    async def run() -> None:
        manager.start_periodic_flush()
        await asyncio.sleep(0.03)
        await manager.stop_periodic_flush()

    asyncio.run(run())

    stats = manager.get_stats()
    assert stats["flushed_batch_count"] == 0
    assert stats["flushed_record_count"] == 0
    assert stats["periodic_flush_count"] == 0


def test_shutdown_flush_flushes_remaining_records() -> None:
    manager = ParsedLogBatchManager(batch_size=500)

    async def run() -> None:
        await manager.add(parsed_log(1))
        await manager.shutdown_flush()

    asyncio.run(run())

    stats = manager.get_stats()
    assert stats["current_buffer_size"] == 0
    assert stats["flushed_batch_count"] == 1
    assert stats["flushed_record_count"] == 1
    assert stats["shutdown_flush_count"] == 1


def test_failed_periodic_sink_does_not_crash_and_retains_records() -> None:
    async def sink(_: list[dict]) -> int:
        raise RuntimeError("periodic database unavailable")

    manager = ParsedLogBatchManager(batch_size=500, flush_interval_seconds=0.01, sink=sink)

    async def run() -> None:
        await manager.add(parsed_log(1))
        manager.start_periodic_flush()
        await asyncio.sleep(0.03)
        await manager.stop_periodic_flush()

    asyncio.run(run())

    stats = manager.get_stats()
    assert stats["flushed_batch_count"] == 0
    assert stats["periodic_flush_count"] == 1
    assert stats["failed_batch_count"] == 1
    assert len(manager.get_failed_batches()) == 1
    assert "RuntimeError: periodic database unavailable" == stats["last_sink_error"]
