from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


def test_websocket_connects_and_receives_system_status() -> None:
    with client.websocket_connect("/ws/telemetry") as websocket:
        event = websocket.receive_json()

    assert event["type"] == "system.status"
    assert isinstance(event["timestamp"], str)
    assert event["payload"] == {
        "status": "connected",
        "message": "LogSentinel telemetry stream active",
    }
