import asyncio
from datetime import datetime, timezone

from backend.app.models import ParsedLog
from backend.app.services.drain_parser import DrainParser
from backend.app.services.batch_manager import ParsedLogBatchManager
from backend.app.services.runtime_dependency_parser import RuntimeDependencyParser, TraceObservation
from backend.app.workers.drain_worker import DrainWorker






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


class MetadataPreservingParser:
    def parse(
        self,
        raw_message: str,
        metadata: dict | None = None,
    ) -> ParsedLog:
        metadata = metadata or {}
        now = datetime.now(timezone.utc)
        return ParsedLog(
            id=f"log-{hash(raw_message)}",
            timestamp=now,
            service=str(metadata.get("service", "test-service")),
            level=str(metadata.get("level", "info")),
            raw_message=raw_message,
            template_id=f"template-{raw_message}",
            template_text=raw_message,
            parameters=[],
            source=metadata.get("source"),
            environment=metadata.get("environment"),
            correlation_id=metadata.get("correlation_id"),
            metadata=dict(metadata),
            parsed_at=now,
        )


class FailingRuntimeDependencyParser(RuntimeDependencyParser):
    def extract(self, parsed_log: ParsedLog) -> TraceObservation | None:
        raise RuntimeError("expected trace extraction failure")


class ShutdownRecordingBatchManager(ParsedLogBatchManager):
    def __init__(self, events: list[str], sink=None) -> None:
        super().__init__(batch_size=500, flush_interval_seconds=60.0, sink=sink)
        self.events = events
        self.shutdown_attempt_count = 0

    async def shutdown_flush(self) -> None:
        self.events.append("batch_shutdown_flush")
        self.shutdown_attempt_count += 1
        await super().shutdown_flush()








def make_parsed_log(
    raw_message: str,
    *,
    service: str = "test-service",
    level: str = "info",
) -> ParsedLog:
    now = datetime.now(timezone.utc)
    return ParsedLog(
        id=f"log-{hash(raw_message)}",
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
    from drain3.file_persistence import FilePersistence
    state_path = str(tmp_path / "drain_worker_state.bin")
    pers = FilePersistence(state_path)
    parser = DrainParser(state_path=state_path, persistence=pers)
    return DrainWorker(None, parser)


def make_worker_with_batch_size(tmp_path, batch_size: int) -> DrainWorker:
    from drain3.file_persistence import FilePersistence
    state_path = str(tmp_path / "drain_worker_state.bin")
    pers = FilePersistence(state_path)
    parser = DrainParser(state_path=state_path, persistence=pers)
    batch_manager = ParsedLogBatchManager(batch_size=batch_size)
    return DrainWorker(None, parser, batch_manager=batch_manager)


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
    from drain3.file_persistence import FilePersistence
    state_path = str(tmp_path / "typed_boundary_state.bin")
    pers = FilePersistence(state_path)
    parser = DrainParser(state_path=state_path, persistence=pers)
    batch_manager = RecordingBatchManager()
    worker = DrainWorker(None, parser, batch_manager=batch_manager)
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

    from drain3.file_persistence import FilePersistence
    state_path = str(tmp_path / "drain_worker_sink_state.bin")
    pers = FilePersistence(state_path)
    parser = DrainParser(state_path=state_path, persistence=pers)
    batch_manager = ParsedLogBatchManager(batch_size=2, sink=fake_sink)
    worker = DrainWorker(None, parser, batch_manager=batch_manager)

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
        from drain3.file_persistence import FilePersistence
        state_path = str(tmp_path / "drain_worker_shutdown_state.bin")
        pers = FilePersistence(state_path)
        parser = DrainParser(state_path=state_path, persistence=pers)
        batch_manager = ParsedLogBatchManager(batch_size=500, sink=fake_sink)
        worker = DrainWorker(None, parser, batch_manager=batch_manager)

        await worker.process_one("shutdown flush log line")
        await worker.stop()
        return worker.get_stats()["batch"]

    batch_stats = asyncio.run(run())

    assert batch_stats["current_buffer_size"] == 0
    assert batch_stats["shutdown_flush_count"] == 1
    assert batch_stats["flushed_record_count"] == 1
    assert batch_stats["last_sink_result"] == 1
    assert len(inserted_batches) == 1





def test_drain_worker_continues_normal_persistence_when_no_trace_context() -> None:
    batch_manager = RecordingBatchManager()
    observations: list[TraceObservation] = []
    worker = DrainWorker(
        None,
        MetadataPreservingParser(),  # type: ignore[arg-type]
        batch_manager=batch_manager,
        runtime_dependency_parser=RuntimeDependencyParser(),
        on_trace_observation=observations.append,
    )

    parsed_logs = asyncio.run(
        worker.process_one({"logs": [{"service_name": "orders", "message": "no trace here"}]})
    )

    assert len(parsed_logs) == 1
    assert batch_manager.received == parsed_logs
    assert observations == []
    assert worker.get_stats()["recent_trace_observation_count"] == 0


def test_drain_worker_passes_extracted_trace_observation_to_injected_collector() -> None:
    batch_manager = RecordingBatchManager()
    observations: list[TraceObservation] = []
    worker = DrainWorker(
        None,
        MetadataPreservingParser(),  # type: ignore[arg-type]
        batch_manager=batch_manager,
        runtime_dependency_parser=RuntimeDependencyParser(),
        on_trace_observation=observations.append,
    )

    parsed_logs = asyncio.run(
        worker.process_one(
            {
                "source": "gateway",
                "environment": "test",
                "logs": [
                    {
                        "service_name": "orders",
                        "message": "trace-bearing log",
                        "metadata": {"trace_id": "worker-trace"},
                    }
                ],
            }
        )
    )

    assert len(parsed_logs) == 1
    assert len(observations) == 1
    assert observations[0].trace_id == "worker-trace"
    assert observations[0].canonical_transaction_id == "worker-trace"
    assert observations[0].service == "orders"
    assert worker.get_recent_trace_observations(limit=1)[0]["trace_id"] == "worker-trace"


def test_trace_extraction_failure_does_not_prevent_parsed_log_batching() -> None:
    batch_manager = RecordingBatchManager()
    observations: list[TraceObservation] = []
    worker = DrainWorker(
        None,
        MetadataPreservingParser(),  # type: ignore[arg-type]
        batch_manager=batch_manager,
        runtime_dependency_parser=FailingRuntimeDependencyParser(),
        on_trace_observation=observations.append,
    )

    parsed_logs = asyncio.run(
        worker.process_one(
            {
                "logs": [
                    {
                        "service_name": "orders",
                        "message": "trace extraction failure is isolated",
                        "metadata": {"trace_id": "worker-trace"},
                    }
                ]
            }
        )
    )

    assert len(parsed_logs) == 1
    assert batch_manager.received == parsed_logs
    assert observations == []
    assert worker.get_stats()["processed_count"] == 1
