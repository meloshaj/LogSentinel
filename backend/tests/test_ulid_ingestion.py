import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app, get_log_buffer, drain_worker
from typing import Any
import json
from unittest.mock import patch, MagicMock

@pytest.fixture(autouse=True)
def ensure_telemetry_loop():
    # Make sure we don't try to broadcast without a running loop
    with patch("app.workers.drain_worker.telemetry_manager.broadcast", new_callable=MagicMock) as mock_broadcast:
        mock_broadcast.return_value = None
        yield mock_broadcast

@pytest.mark.asyncio
async def test_ingestion_ulid_assignment(ensure_telemetry_loop):
    """
    Test that ingested logs are assigned a ULID and that both REST and WebSocket
    outputs use the identical string ID.
    """
    # Create the test payload
    payload = {
        "source": "test-suite",
        "environment": "test",
        "logs": [
            {
                "service_name": "ulid-test-service",
                "message": "Testing ULID assignment 123",
                "level": "INFO",
                "timestamp": "2026-08-05T12:00:00Z"
            }
        ]
    }
    
    # 1. Start drain_worker locally in test (mocking the batch_manager to avoid DB)
    drain_worker._running = False
    
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 2. Ingest log
        response = await client.post("/api/v1/ingest", json=payload)
        assert response.status_code in (200, 202)
        
        # 3. Manually drain the queue once
        buffer = get_log_buffer()
        item = await buffer.dequeue()
        parsed_logs = await drain_worker.process_one(item)
        buffer.task_done()
        
        # Verify the parsed log has a valid ULID
        assert len(parsed_logs) == 1
        parsed = parsed_logs[0]
        assert hasattr(parsed, "id")
        assert isinstance(parsed.id, str)
        assert len(parsed.id) == 26
        
        # 4. Verify telemetry broadcast got the exact same ID
        ensure_telemetry_loop.assert_called_once()
        broadcast_event = ensure_telemetry_loop.call_args[0][0]
        # ensure_telemetry_loop.call_args[0][0] is the Event payload dictionary or string?
        # In telemetry_event, it returns a string `event: ...\ndata: ...`
        assert parsed.id in broadcast_event
        
        # 5. Fetch from REST and verify it matches
        recent_response = await client.get("/api/v1/drain3/recent?limit=5")
        assert recent_response.status_code == 200
        recent_logs = recent_response.json()
        assert len(recent_logs) > 0
        
        # The most recent log should be our test log
        latest_log = recent_logs[0]
        assert latest_log["id"] == parsed.id
