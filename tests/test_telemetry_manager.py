import asyncio
from typing import Any
import pytest

from backend.app.websockets.broadcaster import HighLoadBroadcaster
from backend.app.services.telemetry import telemetry_event

class FakeWebSocket:
    def __init__(self, fail_send: bool = False) -> None:
        self.accepted = False
        self.fail_send = fail_send
        self.sent_events: list[dict[str, Any]] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, event: dict[str, Any]) -> None:
        if self.fail_send:
            raise RuntimeError("client disconnected")
        self.sent_events.append(event)


@pytest.mark.asyncio
async def test_broadcast_to_zero_clients_does_not_crash() -> None:
    manager = HighLoadBroadcaster()
    await manager.broadcast(telemetry_event("system.status", {"status": "ok"}))
    assert manager.connection_count() == 0
    await manager.stop()


@pytest.mark.asyncio
async def test_connected_client_receives_broadcast_event() -> None:
    manager = HighLoadBroadcaster(frame_rate_ms=10.0)
    websocket = FakeWebSocket()
    event = telemetry_event("log.parsed", {"service": "api-gateway"})

    await websocket.accept()
    await manager.connect(websocket)  # type: ignore[arg-type]
    await manager.broadcast(event)
    
    # Wait for the flush loop to run
    await asyncio.sleep(0.05)

    assert websocket.accepted is True
    assert len(websocket.sent_events) >= 1
    
    frame = websocket.sent_events[0]
    assert frame["type"] == "frame_update"
    assert "timestamp" in frame
    assert frame["payload"]["events"] == [event]
    
    assert manager.connection_count() == 1
    await manager.stop()


@pytest.mark.asyncio
async def test_failed_send_removes_stale_client_without_breaking_others() -> None:
    manager = HighLoadBroadcaster(frame_rate_ms=10.0)
    stale = FakeWebSocket(fail_send=True)
    healthy = FakeWebSocket()
    event = telemetry_event("feature.window.closed", {"window_id": "window-1"})

    await manager.connect(stale)  # type: ignore[arg-type]
    await manager.connect(healthy)  # type: ignore[arg-type]
    await manager.broadcast(event)

    # Wait for flush loop
    await asyncio.sleep(0.05)

    assert len(healthy.sent_events) >= 1
    frame = healthy.sent_events[0]
    assert frame["payload"]["events"] == [event]

    # Stale connection should have been removed
    assert manager.connection_count() == 1
    await manager.stop()


@pytest.mark.asyncio
async def test_disconnect_cleanup_does_not_raise() -> None:
    manager = HighLoadBroadcaster()
    websocket = FakeWebSocket()

    await manager.connect(websocket)  # type: ignore[arg-type]
    await manager.disconnect(websocket)  # type: ignore[arg-type]
    await manager.disconnect(websocket)  # type: ignore[arg-type]

    assert manager.connection_count() == 0
    await manager.stop()
