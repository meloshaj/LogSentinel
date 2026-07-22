import asyncio
from datetime import datetime

from backend.app.models import ParsedLog
from backend.app.services.drain_parser import DrainParser
from backend.app.services.batch_manager import ParsedLogBatchManager
from backend.app.workers.drain_worker import DrainWorker


class StubLogBuffer:
    def __init__(self) -> None:
        self.items = asyncio.Queue()

    async def dequeue(self):
        return await self.items.get()

    def queue_size(self) -> int:
        return self.items.qsize()


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
