from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


@patch("jwt.decode")
def test_websocket_connects_and_receives_system_status(mock_decode) -> None:
    mock_decode.return_value = {"sub": "test"}
    with client.websocket_connect("/ws/telemetry") as websocket:
        websocket.send_json({"type": "auth", "token": "test_token"})
        event = websocket.receive_json()

    assert event["type"] == "system.status"
    assert isinstance(event["timestamp"], str)
    assert event["payload"] == {
        "status": "connected",
        "message": "LogSentinel telemetry stream active",
    }
