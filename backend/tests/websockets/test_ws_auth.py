import time
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from backend.app.main import app
from backend.app.security.auth import create_access_token

client = TestClient(app)

def test_websocket_handshake_timeout():
    """Connect an unauthenticated WebSocket client without sending auth frame."""
    start = time.time()
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/telemetry") as websocket:
            # We don't send anything. Just try to receive.
            # The server waits 5.0 seconds for an auth frame and then closes.
            websocket.receive_text()

    duration = time.time() - start
    assert exc.value.code == 1008
    # Assert duration is roughly 5 seconds (give some padding for overhead)
    assert 4.0 <= duration <= 10.0

def test_websocket_invalid_jwt_disconnect():
    """Connect a client that sends an invalid or expired JWT."""
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/telemetry") as websocket:
            websocket.send_json({"type": "auth", "token": "invalid_jwt_string"})
            websocket.receive_text()

    assert exc.value.code == 1008

def test_websocket_valid_client_connects():
    """Connect a valid client; verify it transitions to the active telemetry broadcast pool."""
    valid_token = create_access_token(data={"sub": "test@example.com"})
    with client.websocket_connect("/ws/telemetry") as websocket:
        websocket.send_json({"type": "auth", "token": valid_token})
        
        # We should receive the system status "connected" event
        response = websocket.receive_json()
        assert response.get("type") == "system.status"
        assert response.get("payload", {}).get("status") == "connected"
