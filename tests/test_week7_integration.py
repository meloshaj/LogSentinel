import asyncio
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from backend.app.websockets.broadcaster import HighLoadBroadcaster
from backend.app.services.benchmarking import BenchmarkingCollector
from backend.app.services.telemetry import telemetry_event

@pytest.mark.asyncio
async def test_high_load_broadcaster_throttling():
    """Test that the HighLoadBroadcaster correctly batches messages under high load."""
    broadcaster = HighLoadBroadcaster(frame_rate_ms=250)
    
    # Mock a websocket connection
    mock_ws = AsyncMock()
    
    await broadcaster.connect(mock_ws)
    
    # Simulate a burst of 1000 events
    events = [telemetry_event("infrastructure.tracking_loop.triggered", {"id": i}) for i in range(1000)]
    
    # Broadcast all events concurrently
    await asyncio.gather(*(broadcaster.broadcast(e) for e in events))
    
    # Wait for the flush loop to run (at least 250ms + margin)
    await asyncio.sleep(0.4)
    
    # Assert that send_json was called 
    assert mock_ws.send_json.called
    payload = mock_ws.send_json.call_args[0][0]
    
    assert payload["type"] == "frame_update"
    assert "events" in payload["payload"]
    # Verify that batched events are present
    assert len(payload["payload"]["events"]) > 0
    
    # Clean up
    await broadcaster.stop()


def test_benchmarking_collector_integration():
    """Test that BenchmarkingCollector correctly aggregates metrics."""
    collector = BenchmarkingCollector()
    
    # Record synthetic metrics
    for _ in range(50):
        collector.record_db_batch_duration(15.5)
        collector.set_queue_depth(100)
    
    for _ in range(50):
        collector.record_db_batch_duration(25.0)
        collector.set_queue_depth(200)
        
    health = collector.get_health_metrics()
    
    assert "db_batch_duration_ms" in health
    assert "queue_depth" in health
    
    assert isinstance(health["db_batch_duration_ms"], float)
    assert health["db_batch_duration_ms"] > 15.5
    assert health["queue_depth"] == 200
