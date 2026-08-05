import asyncio

from backend.app.websockets.broadcaster import HighLoadBroadcaster
from backend.app.services.telemetry import telemetry_event


class FakeWebSocket:
    def __init__(self, fail_send: bool = False) -> None:
        self.accepted = False
        self.fail_send = fail_send
        self.sent_events: list[dict] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, event: dict) -> None:
        if self.fail_send:
            raise RuntimeError("client disconnected")
        self.sent_events.append(event)


def test_broadcast_to_zero_clients_does_not_crash() -> None:
    manager = HighLoadBroadcaster()

    asyncio.run(manager.broadcast(telemetry_event("system.status", {"status": "ok"})))

    assert manager.connection_count() == 0


def test_connected_client_receives_broadcast_event() -> None:
    manager = HighLoadBroadcaster()
    websocket = FakeWebSocket()
    event = telemetry_event("log.parsed", {"service": "api-gateway"})

    async def run() -> None:
        await manager.connect(websocket)  # type: ignore[arg-type]
        await manager.broadcast(event)

    asyncio.run(run())

    assert websocket.accepted is True
    assert websocket.sent_events == [event]
    assert manager.connection_count() == 1


def test_failed_send_removes_stale_client_without_breaking_others() -> None:
    manager = HighLoadBroadcaster()
    stale = FakeWebSocket(fail_send=True)
    healthy = FakeWebSocket()
    event = telemetry_event("feature.window.closed", {"window_id": "window-1"})

    async def run() -> None:
        await manager.connect(stale)  # type: ignore[arg-type]
        await manager.connect(healthy)  # type: ignore[arg-type]
        await manager.broadcast(event)

    asyncio.run(run())

    assert healthy.sent_events == [event]
    assert manager.connection_count() == 1


def test_disconnect_cleanup_does_not_raise() -> None:
    manager = HighLoadBroadcaster()
    websocket = FakeWebSocket()

    async def run() -> None:
        await manager.connect(websocket)  # type: ignore[arg-type]
        await manager.disconnect(websocket)  # type: ignore[arg-type]
        await manager.disconnect(websocket)  # type: ignore[arg-type]

    asyncio.run(run())

    assert manager.connection_count() == 0
