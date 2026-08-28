import asyncio
import json
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.models import ParsedLog
from backend.app.services.batch_manager import ParsedLogBatchManager
from backend.app.workers.drain_worker import DrainWorker, DLQ_STREAM_NAME


class ExplodingParser:
    """A parser that always raises an unrecoverable exception to simulate a poison pill."""

    def __init__(self, exception_msg: str = "Invalid syntax in raw log for Drain3") -> None:
        self.exception_msg = exception_msg
        self.call_count = 0
        self._miner = MagicMock()

    def parse(self, raw_message: str, metadata: dict | None = None) -> ParsedLog:
        self.call_count += 1
        raise ValueError(f"{self.exception_msg}: {raw_message}")


class RecoveringParser:
    """A parser that fails twice and succeeds on the third attempt."""

    def __init__(self) -> None:
        self.call_count = 0
        self._miner = MagicMock()

    def parse(self, raw_message: str, metadata: dict | None = None) -> ParsedLog:
        self.call_count += 1
        if self.call_count <= 2:
            raise ValueError(f"Temporary parse error on attempt {self.call_count}")
        now = datetime.now(timezone.utc)
        return ParsedLog(
            id=f"log-{hash(raw_message)}",
            timestamp=now,
            service="test-service",
            level="info",
            raw_message=raw_message,
            template_id="template-1",
            template_text=raw_message,
            parsed_at=now,
        )


@pytest.mark.asyncio
async def test_poison_pill_escalates_to_dlq_after_three_failures(caplog) -> None:
    """When Drain3 parsing fails 3 consecutive times, the entry is forwarded to logs:dlq and XACKed."""
    caplog.set_level(logging.ERROR)
    parser = ExplodingParser("Drain3 parse explosion")
    batch_manager = ParsedLogBatchManager()
    worker = DrainWorker(
        log_buffer=None,
        parser=parser,  # type: ignore[arg-type]
        batch_manager=batch_manager,
    )

    mock_redis = MagicMock()
    mock_redis.xadd = AsyncMock(return_value="dlq-msg-1")
    mock_redis.xack = AsyncMock(return_value=1)
    mock_redis.incr = AsyncMock(side_effect=[1, 2, 3])
    mock_redis.expire = AsyncMock(return_value=True)
    mock_redis.delete = AsyncMock(return_value=1)
    worker.set_redis_client(mock_redis)

    poison_payload = {
        "source": "api-gateway",
        "environment": "production",
        "correlation_id": "corr-poison-123",
        "logs": [
            {
                "service_name": "billing",
                "level": "error",
                "message": "<<MALFORMED_DRAIN_PAYLOAD>>",
            }
        ],
    }

    # Run 3 consecutive attempts for the same log item
    await worker.process_one(poison_payload, message_id="1001-0")
    assert worker.dlq_count == 0
    assert mock_redis.xadd.call_count == 0

    await worker.process_one(poison_payload, message_id="1001-0")
    assert worker.dlq_count == 0
    assert mock_redis.xadd.call_count == 0

    # 3rd failure triggers DLQ and XACK
    await worker.process_one(poison_payload, message_id="1001-0")
    assert worker.dlq_count == 1
    assert mock_redis.xadd.call_count == 1

    # Verify DLQ payload arguments
    call_args = mock_redis.xadd.call_args
    assert call_args[0][0] == DLQ_STREAM_NAME
    dlq_payload = call_args[0][1]
    assert dlq_payload["payload"] == "<<MALFORMED_DRAIN_PAYLOAD>>"
    assert "Drain3 parse explosion" in dlq_payload["error"]
    assert dlq_payload["log_id"] == "corr-poison-123"
    assert dlq_payload["stream_message_id"] == "1001-0"

    # Verify XACK was sent for the poisoned message
    mock_redis.xack.assert_called_with(worker.stream_name, worker.group_name, "1001-0")

    # Verify structured logging occurred with snippet and ID
    assert any("Poison pill detected" in record.message for record in caplog.records)
    assert any("corr-poison-123" in str(record.__dict__) for record in caplog.records)
    assert any("MALFORMED_DRAIN_PAYLOAD" in str(record.__dict__) for record in caplog.records)


@pytest.mark.asyncio
async def test_corrupted_json_stream_message_routes_to_dlq(caplog) -> None:
    """When stream message has corrupted unparseable JSON, it is forwarded to DLQ after 3 retries and XACKed."""
    caplog.set_level(logging.ERROR)
    worker = DrainWorker(log_buffer=None, parser=MagicMock())

    mock_redis = MagicMock()
    mock_redis.xadd = AsyncMock(return_value="dlq-corrupted-1")
    mock_redis.xack = AsyncMock(return_value=1)
    mock_redis.incr = AsyncMock(side_effect=[1, 2, 3])
    mock_redis.expire = AsyncMock(return_value=True)
    mock_redis.delete = AsyncMock(return_value=1)
    worker.set_redis_client(mock_redis)

    corrupted_entry = {b"payload": b"NOT_VALID_JSON{{"}

    await worker._process_stream_message("msg-999", corrupted_entry)
    await worker._process_stream_message("msg-999", corrupted_entry)
    await worker._process_stream_message("msg-999", corrupted_entry)

    assert worker.dlq_count == 1
    assert mock_redis.xadd.call_count == 1
    call_args = mock_redis.xadd.call_args
    assert call_args[0][0] == DLQ_STREAM_NAME
    assert call_args[0][1]["payload"] == "NOT_VALID_JSON{{"
    mock_redis.xack.assert_called_with(worker.stream_name, worker.group_name, "msg-999")


@pytest.mark.asyncio
async def test_successful_parse_clears_retry_count() -> None:
    """When a message parses successfully after a transient failure, retry tracking is cleared."""
    parser = RecoveringParser()
    worker = DrainWorker(log_buffer=None, parser=parser)  # type: ignore[arg-type]

    mock_redis = MagicMock()
    mock_redis.incr = AsyncMock(side_effect=[1, 2])
    mock_redis.expire = AsyncMock(return_value=True)
    mock_redis.delete = AsyncMock(return_value=1)
    worker.set_redis_client(mock_redis)

    item = {
        "logs": [{"service_name": "auth", "message": "auth log line", "id": "log-auth-1"}]
    }

    # Attempt 1 -> fails
    res1 = await worker.process_one(item)
    assert len(res1) == 0

    # Attempt 2 -> fails
    res2 = await worker.process_one(item)
    assert len(res2) == 0

    # Attempt 3 -> succeeds
    res3 = await worker.process_one(item)
    assert len(res3) == 1
    assert res3[0].raw_message == "auth log line"
    assert worker.dlq_count == 0
    assert mock_redis.delete.call_count >= 1


def test_approximate_stream_trimming_on_ingest(monkeypatch) -> None:
    """Verify that ingest endpoint uses approximate stream trimming MAXLEN ~ 500000 on XADD."""
    xadd_calls = []

    class MockRedisPipeline:
        def xadd(self, stream, payload, maxlen=None, approximate=None):
            xadd_calls.append({"stream": stream, "payload": payload, "maxlen": maxlen, "approximate": approximate})

        def xlen(self, stream):
            return 42

        async def execute(self):
            return ["1700000000-0", 42]

    class MockRedis:
        def pipeline(self, transaction=False):
            return MockRedisPipeline()

    monkeypatch.setattr(app.state, "redis", MockRedis(), raising=False)
    client = TestClient(app)

    with patch.dict("os.environ", {"INGEST_API_KEY": "test-key"}, clear=False):
        response = client.post(
            "/ingest-log",
            json={
                "source": "api-gateway",
                "environment": "production",
                "logs": [{"service_name": "orders", "level": "info", "message": "Order created"}],
            },
            headers={"X-API-Key": "test-key"},
        )

    assert response.status_code == 202
    assert len(xadd_calls) == 1
    assert xadd_calls[0]["stream"] == "logs:stream"
    assert xadd_calls[0]["maxlen"] == 500000
    assert xadd_calls[0]["approximate"] is True
