import asyncio
from datetime import datetime, timezone

from backend.app.main import AsyncLogBuffer
from backend.app.models import ParsedLog
from backend.app.services.drain_parser import DrainParser
from backend.app.services.batch_manager import ParsedLogBatchManager
from backend.app.workers.drain_worker import DrainWorker


class StubLogBuffer:
    def __init__(self) -> None:
        self.items = asyncio.Queue()

    async def dequeue(self):
        return await self.items.get()

    async def join(self) -> None:
        await self.items.join()

    def task_done(self) -> None:
        self.items.task_done()

    def queue_size(self) -> int:
        return self.items.qsize()


class CountingAsyncLogBuffer(AsyncLogBuffer):
    def __init__(self) -> None:
        super().__init__()
        self.task_done_count = 0

    def task_done(self) -> None:
        self.task_done_count += 1
        super().task_done()


class FakeParser:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events if events is not None else []

    def parse(
        self,
        raw_message: str,
        metadata: dict | None = None,
    ) -> ParsedLog:
        self.events.append(f"processed:{raw_message}")
        metadata = metadata or {}
        return make_parsed_log(
            raw_message,
            service=str(metadata.get("service", "test-service")),
            level=str(metadata.get("level", "info")),
        )


class ShutdownRecordingBatchManager(ParsedLogBatchManager):
    def __init__(self, events: list[str], sink=None) -> None:
        super().__init__(batch_size=500, flush_interval_seconds=60.0, sink=sink)
        self.events = events
        self.shutdown_attempt_count = 0

    async def shutdown_flush(self) -> None:
        self.events.append("batch_shutdown_flush")
        self.shutdown_attempt_count += 1
        await super().shutdown_flush()


class BlockingDrainWorker(DrainWorker):
    def __init__(self, *args, entered: asyncio.Event, release: asyncio.Event, **kwargs):
        super().__init__(*args, **kwargs)
        self.entered = entered
        self.release = release
        self.completed_items: list[object] = []

    async def process_one(self, item):
        self.entered.set()
        await self.release.wait()
        self.completed_items.append(item)
        return []


class FailingDrainWorker(DrainWorker):
    async def process_one(self, _item):
        raise RuntimeError("expected processing failure")


def make_parsed_log(
    raw_message: str,
    *,
    service: str = "test-service",
    level: str = "info",
) -> ParsedLog:
    now = datetime.now(timezone.utc)
    return ParsedLog(
        timestamp=now,
        service=service,
        level=level,
        raw_message=raw_message,
        template_id=f"template-{raw_message}",
        template_text=raw_message,
        parsed_at=now,
    )


class RecordingBatchManager(ParsedLogBatchManager):
    """Capture exactly what DrainWorker passes to the batching boundary."""

    def __init__(self) -> None:
        super().__init__()
        self.received: list[ParsedLog] = []

    async def add(self, parsed_log: ParsedLog) -> None:
        self.received.append(parsed_log)


def make_worker(tmp_path) -> DrainWorker:
    parser = DrainParser(state_path=str(tmp_path / "drain_worker_state.bin"))
    return DrainWorker(StubLogBuffer(), parser)


def make_worker_with_batch_size(tmp_path, batch_size: int) -> DrainWorker:
    parser = DrainParser(state_path=str(tmp_path / "drain_worker_state.bin"))
    batch_manager = ParsedLogBatchManager(batch_size=batch_size)
    return DrainWorker(StubLogBuffer(), parser, batch_manager=batch_manager)


def test_drain_worker_processes_sample_payload(tmp_path) -> None:
    worker = make_worker(tmp_path)

    parsed_logs = asyncio.run(
        worker.process_one(
            {
                "source": "api-gateway",
                "environment": "test",
                "correlation_id": "corr-123",
                "logs": [
                    {
                        "service_name": "service-a",
                        "level": "error",
                        "message": "service-a failed to connect to 10.0.0.1 on port 5432",
                    }
                ],
            }
        )
    )

    assert worker.get_stats()["processed_count"] == 1
    assert isinstance(parsed_logs[0], ParsedLog)
    assert parsed_logs[0]["template_id"]
    assert parsed_logs[0]["template_text"]
    assert parsed_logs[0]["metadata"]["service"] == "service-a"
    assert parsed_logs[0]["metadata"]["correlation_id"] == "corr-123"
    assert worker.get_stats()["batch"]["current_buffer_size"] == 1


def test_drain_worker_tracks_unsupported_payload_errors(tmp_path) -> None:
    worker = make_worker(tmp_path)

    parsed_logs = asyncio.run(worker.process_one({"logs": [{"no_message": True}]}))

    assert parsed_logs == []
    assert worker.get_stats()["error_count"] == 1


def test_drain_worker_tracks_unsupported_entries_in_mixed_payload(tmp_path) -> None:
    worker = make_worker(tmp_path)

    parsed_logs = asyncio.run(
        worker.process_one(
            {
                "logs": [
                    {"message": "service-a failed to connect to 10.0.0.1 on port 5432"},
                    {"no_message": True},
                ]
            }
        )
    )

    stats = worker.get_stats()
    assert len(parsed_logs) == 1
    assert stats["processed_count"] == 1
    assert stats["error_count"] == 1


def test_drain_worker_recent_logs_are_newest_first(tmp_path) -> None:
    worker = make_worker(tmp_path)

    asyncio.run(worker.process_one("first log line"))
    asyncio.run(worker.process_one("second log line"))

    recent = worker.get_recent_parsed_logs(limit=2)

    assert [entry["raw_message"] for entry in recent] == ["second log line", "first log line"]


def test_drain_worker_sends_parsed_logs_to_batch_manager(tmp_path) -> None:
    worker = make_worker_with_batch_size(tmp_path, batch_size=2)

    asyncio.run(worker.process_one("first log line"))
    asyncio.run(worker.process_one("second log line"))

    batch_stats = worker.get_stats()["batch"]
    assert batch_stats["current_buffer_size"] == 0
    assert batch_stats["flushed_batch_count"] == 1
    assert batch_stats["flushed_record_count"] == 2


def test_drain_worker_passes_typed_parsed_log_without_serializing(tmp_path) -> None:
    parser = DrainParser(state_path=str(tmp_path / "typed_boundary_state.bin"))
    batch_manager = RecordingBatchManager()
    worker = DrainWorker(StubLogBuffer(), parser, batch_manager=batch_manager)
    timestamp = "2026-07-22T18:30:00+00:00"

    parsed_logs = asyncio.run(
        worker.process_one(
            {
                "source": "typed-test",
                "environment": "test",
                "correlation_id": "typed-correlation",
                "logs": [
                    {
                        "service_name": "orders",
                        "level": "warning",
                        "timestamp": timestamp,
                        "raw": "order 42 timed out after 5000ms",
                        "message": "fallback message",
                        "metadata": {"request_id": "request-42"},
                    }
                ],
            }
        )
    )

    assert len(parsed_logs) == 1
    parsed = parsed_logs[0]
    assert isinstance(parsed, ParsedLog)
    assert batch_manager.received == [parsed]
    assert batch_manager.received[0] is parsed
    assert parsed.raw_message == "order 42 timed out after 5000ms"
    assert parsed.template_id
    assert parsed.template_text
    assert parsed.timestamp == datetime.fromisoformat(timestamp)
    assert parsed.service == "orders"
    assert parsed.level == "warning"
    assert parsed.metadata["request_id"] == "request-42"
    assert parsed.metadata["source"] == "typed-test"
    assert parsed.metadata["correlation_id"] == "typed-correlation"


def test_drain_worker_flushes_parsed_logs_to_fake_sink(tmp_path) -> None:
    inserted_batches: list[list[ParsedLog]] = []

    async def fake_sink(batch: list[ParsedLog]) -> int:
        inserted_batches.append(batch)
        return len(batch)

    parser = DrainParser(state_path=str(tmp_path / "drain_worker_sink_state.bin"))
    batch_manager = ParsedLogBatchManager(batch_size=2, sink=fake_sink)
    worker = DrainWorker(StubLogBuffer(), parser, batch_manager=batch_manager)

    asyncio.run(worker.process_one("first log line"))
    asyncio.run(worker.process_one("second log line"))

    batch_stats = worker.get_stats()["batch"]
    assert batch_stats["last_sink_result"] == 2
    assert batch_stats["flushed_record_count"] == 2
    assert len(inserted_batches) == 1
    assert isinstance(inserted_batches[0][0], ParsedLog)
    assert inserted_batches[0][0].template_id


def test_drain_worker_shutdown_flushes_remaining_batch_records(tmp_path) -> None:
    inserted_batches: list[list[ParsedLog]] = []

    async def fake_sink(batch: list[ParsedLog]) -> int:
        inserted_batches.append(batch)
        return len(batch)

    async def run() -> dict:
        parser = DrainParser(state_path=str(tmp_path / "drain_worker_shutdown_state.bin"))
        batch_manager = ParsedLogBatchManager(batch_size=500, sink=fake_sink)
        worker = DrainWorker(StubLogBuffer(), parser, batch_manager=batch_manager)

        await worker.process_one("shutdown flush log line")
        await worker.stop()
        return worker.get_stats()["batch"]

    batch_stats = asyncio.run(run())

    assert batch_stats["current_buffer_size"] == 0
    assert batch_stats["shutdown_flush_count"] == 1
    assert batch_stats["flushed_record_count"] == 1
    assert batch_stats["last_sink_result"] == 1
    assert len(inserted_batches) == 1


def test_drain_worker_drains_all_accepted_payloads_before_shutdown_flush() -> None:
    async def run() -> tuple[list[str], list[str], int, dict]:
        events: list[str] = []
        persisted_messages: list[str] = []

        async def sink(batch: list[ParsedLog]) -> int:
            persisted_messages.extend(record.raw_message for record in batch)
            return len(batch)

        log_buffer = CountingAsyncLogBuffer()
        parser = FakeParser(events)
        batch_manager = ShutdownRecordingBatchManager(events, sink=sink)
        worker = DrainWorker(
            log_buffer,
            parser,  # type: ignore[arg-type]
            batch_manager=batch_manager,
            queue_drain_timeout_seconds=0.5,
        )
        messages = ["first", "second", "third"]
        for message in messages:
            assert log_buffer.enqueue({"logs": [{"message": message}]})

        worker.start()
        await worker.stop()
        assert worker._task is None
        return events, persisted_messages, log_buffer.task_done_count, worker.get_stats()

    events, persisted_messages, task_done_count, stats = asyncio.run(run())

    assert events[:3] == ["processed:first", "processed:second", "processed:third"]
    assert events[-1] == "batch_shutdown_flush"
    assert persisted_messages == ["first", "second", "third"]
    assert task_done_count == 3
    assert stats["processed_count"] == 3
    assert stats["running"] is False
    assert stats["last_queue_drain_timed_out"] is False


def test_drain_worker_stop_waits_for_in_flight_payload() -> None:
    async def run() -> tuple[bool, list[object], int]:
        entered = asyncio.Event()
        release = asyncio.Event()
        log_buffer = CountingAsyncLogBuffer()
        worker = BlockingDrainWorker(
            log_buffer,
            FakeParser(),  # type: ignore[arg-type]
            batch_manager=ParsedLogBatchManager(flush_interval_seconds=60.0),
            queue_drain_timeout_seconds=0.5,
            entered=entered,
            release=release,
        )
        payload = {"logs": [{"message": "blocked"}]}
        assert log_buffer.enqueue(payload)
        worker.start()
        await asyncio.wait_for(entered.wait(), timeout=0.2)

        stop_task = asyncio.create_task(worker.stop())
        await asyncio.sleep(0)
        was_waiting = not stop_task.done()
        release.set()
        await asyncio.wait_for(stop_task, timeout=0.2)
        assert worker._task is None
        return was_waiting, worker.completed_items, log_buffer.task_done_count

    was_waiting, completed_items, task_done_count = asyncio.run(run())

    assert was_waiting is True
    assert len(completed_items) == 1
    assert task_done_count == 1


def test_drain_worker_with_empty_queue_stops_promptly() -> None:
    async def run() -> tuple[dict, int]:
        log_buffer = CountingAsyncLogBuffer()
        worker = DrainWorker(
            log_buffer,
            FakeParser(),  # type: ignore[arg-type]
            batch_manager=ParsedLogBatchManager(flush_interval_seconds=60.0),
            queue_drain_timeout_seconds=0.5,
        )
        worker.start()
        await asyncio.wait_for(worker.stop(), timeout=0.2)
        assert worker._task is None
        return worker.get_stats(), log_buffer.task_done_count

    stats, task_done_count = asyncio.run(run())

    assert stats["running"] is False
    assert stats["last_queue_drain_timed_out"] is False
    assert stats["batch"]["current_buffer_size"] == 0
    assert task_done_count == 0


def test_drain_worker_marks_failed_processing_complete_exactly_once() -> None:
    async def run() -> tuple[dict, int]:
        log_buffer = CountingAsyncLogBuffer()
        worker = FailingDrainWorker(
            log_buffer,
            FakeParser(),  # type: ignore[arg-type]
            batch_manager=ParsedLogBatchManager(flush_interval_seconds=60.0),
            queue_drain_timeout_seconds=0.5,
        )
        assert log_buffer.enqueue({"logs": [{"message": "will-fail"}]})
        worker.start()
        await worker.stop()
        assert worker._task is None
        return worker.get_stats(), log_buffer.task_done_count

    stats, task_done_count = asyncio.run(run())

    assert stats["error_count"] == 1
    assert stats["last_queue_drain_timed_out"] is False
    assert task_done_count == 1


def test_drain_worker_timeout_stops_consumer_and_attempts_final_flush() -> None:
    async def run() -> tuple[dict, int, int, list[str]]:
        entered = asyncio.Event()
        never_release = asyncio.Event()
        events: list[str] = []
        persisted_messages: list[str] = []

        async def sink(batch: list[ParsedLog]) -> int:
            persisted_messages.extend(record.raw_message for record in batch)
            return len(batch)

        log_buffer = CountingAsyncLogBuffer()
        batch_manager = ShutdownRecordingBatchManager(events, sink=sink)
        await batch_manager.add(make_parsed_log("already-parsed"))
        worker = BlockingDrainWorker(
            log_buffer,
            FakeParser(),  # type: ignore[arg-type]
            batch_manager=batch_manager,
            queue_drain_timeout_seconds=0.01,
            entered=entered,
            release=never_release,
        )
        assert log_buffer.enqueue({"logs": [{"message": "in-flight"}]})
        assert log_buffer.enqueue({"logs": [{"message": "still-queued"}]})
        worker.start()
        await asyncio.wait_for(entered.wait(), timeout=0.2)

        await asyncio.wait_for(worker.stop(), timeout=0.3)
        assert worker._task is None
        return (
            worker.get_stats(),
            log_buffer.task_done_count,
            batch_manager.shutdown_attempt_count,
            persisted_messages,
        )

    stats, task_done_count, shutdown_attempt_count, persisted_messages = asyncio.run(run())

    assert stats["last_queue_drain_timed_out"] is True
    assert stats["queue_size"] == 1
    assert stats["running"] is False
    assert task_done_count == 1
    assert shutdown_attempt_count == 1
    assert persisted_messages == ["already-parsed"]
    assert stats["batch"]["current_buffer_size"] == 0
