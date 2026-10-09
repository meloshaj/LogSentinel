import asyncio
from types import SimpleNamespace
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app.core.orm import TenantMembershipRecord, TenantRecord, UserRecord
from backend.app.main import app
from backend.app.security.auth import create_access_token
from backend.app.security.data_scope import DataScope
from backend.app.security.tenant_context import TenantContext
from backend.app.services.telemetry import telemetry_event
from backend.app.websockets.broadcaster import HighLoadBroadcaster


client = TestClient(app)
_MISSING = object()


class _WebSocketAuthDB:
    """Small database double that preserves the production lookup sequence."""

    def __init__(
        self,
        user: UserRecord,
        tenant: TenantRecord | None,
        membership: TenantMembershipRecord | None,
    ) -> None:
        self.user = user
        self.tenant = tenant
        self.membership = membership

    async def execute(self, statement):
        result = SimpleNamespace()
        result.scalar_one_or_none = lambda: self.user
        return result

    async def scalar(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is TenantRecord:
            return (
                self.tenant
                if self.tenant is not None and self.tenant.id == self.user.tenant_id
                else None
            )
        if entity is TenantMembershipRecord:
            return (
                self.membership
                if self.membership is not None
                and self.membership.tenant_id == self.user.tenant_id
                and self.membership.user_id == self.user.id
                else None
            )
        return None


def _session_factory(db: _WebSocketAuthDB):
    async def session():
        yield db

    return session


def _user(*, user_id: int = 101, tenant_id: str = "tenant-a", status: str = "active") -> UserRecord:
    return UserRecord(
        id=user_id,
        email=f"user-{user_id}@example.test",
        hashed_password="hash",
        full_name=f"User {user_id}",
        organization="Test Org",
        tenant_id=tenant_id,
        status=status,
        role="viewer",
    )


def _tenant(*, tenant_id: str = "tenant-a", status: str = "active") -> TenantRecord:
    return TenantRecord(id=tenant_id, name=tenant_id, status=status)


def _membership(
    *, user_id: int = 101, tenant_id: str = "tenant-a", status: str = "active"
) -> TenantMembershipRecord:
    return TenantMembershipRecord(
        tenant_id=tenant_id, user_id=user_id, role="viewer", status=status
    )


def _authenticated_db(
    *,
    user: UserRecord | None = None,
    tenant: TenantRecord | None | object = _MISSING,
    membership: TenantMembershipRecord | None | object = _MISSING,
) -> _WebSocketAuthDB:
    return _WebSocketAuthDB(
        user or _user(),
        tenant if tenant is not _MISSING else _tenant(),
        membership if membership is not _MISSING else _membership(),
    )


def test_websocket_handshake_timeout():
    """Connect an unauthenticated WebSocket client without sending auth frame."""
    start = time.time()
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/ws/telemetry") as websocket,
    ):
        websocket.receive_text()

    duration = time.time() - start
    assert exc.value.code == 1008
    assert 4.0 <= duration <= 10.0


def test_websocket_invalid_jwt_disconnect(monkeypatch):
    """A token that cannot authenticate never reaches the telemetry pool."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    db = _authenticated_db()
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        monkeypatch.context() as patch_context,
    ):
        patch_context.setattr("backend.app.main.get_async_session", _session_factory(db))
        with client.websocket_connect("/ws/telemetry") as websocket:
            websocket.send_json({"type": "auth", "token": "invalid_jwt_string"})
            websocket.receive_text()

    assert exc.value.code == 1008


def test_websocket_connects_and_receives_system_status(monkeypatch):
    """A valid JWT plus active DB-backed membership receives the system event."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    db = _authenticated_db()
    token = create_access_token(data={"sub": db.user.email})

    with monkeypatch.context() as patch_context:
        patch_context.setattr("backend.app.main.get_async_session", _session_factory(db))
        with client.websocket_connect("/ws/telemetry") as websocket:
            websocket.send_json({"type": "auth", "token": token})
            response = websocket.receive_json()

    assert response["type"] == "system.status"
    assert isinstance(response["timestamp"], str)
    assert response["payload"] == {
        "status": "connected",
        "message": "LogSentinel telemetry stream active",
    }


def test_websocket_valid_client_requires_membership_tenant_and_active_state(monkeypatch):
    """Missing, suspended, inactive, and cross-tenant authority all fail closed."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    cases = [
        ("no-membership", _authenticated_db(membership=None)),
        ("suspended-membership", _authenticated_db(membership=_membership(status="suspended"))),
        ("inactive-tenant", _authenticated_db(tenant=_tenant(status="suspended"))),
        (
            "wrong-tenant",
            _authenticated_db(
                membership=_membership(tenant_id="tenant-b"),
            ),
        ),
    ]

    for _, db in cases:
        token = create_access_token(data={"sub": db.user.email})
        with (
            pytest.raises(WebSocketDisconnect) as exc,
            monkeypatch.context() as patch_context,
        ):
            patch_context.setattr("backend.app.main.get_async_session", _session_factory(db))
            with client.websocket_connect("/ws/telemetry") as websocket:
                websocket.send_json({"type": "auth", "token": token})
                websocket.receive_text()
        assert exc.value.code == 1008


def test_suspended_user_cannot_open_websocket(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    db = _authenticated_db(user=_user(status="suspended"))
    token = create_access_token(data={"sub": db.user.email})

    with (
        pytest.raises(WebSocketDisconnect) as exc,
        monkeypatch.context() as patch_context,
    ):
        patch_context.setattr("backend.app.main.get_async_session", _session_factory(db))
        with client.websocket_connect("/ws/telemetry") as websocket:
            websocket.send_json({"type": "auth", "token": token})
            websocket.receive_text()

    assert exc.value.code == 1008


class _FakeWebSocket:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def send_json(self, event: dict) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_multi_user_websocket_and_datascope_isolation():
    """Tenant A users and Tenant B never receive another owner's event."""
    broadcaster = HighLoadBroadcaster(frame_rate_ms=1)
    socket_a1 = _FakeWebSocket()
    socket_a2 = _FakeWebSocket()
    socket_b1 = _FakeWebSocket()
    await broadcaster.connect(socket_a1, tenant_id="tenant-a", user_id=1)  # type: ignore[arg-type]
    await broadcaster.connect(socket_a2, tenant_id="tenant-a", user_id=2)  # type: ignore[arg-type]
    await broadcaster.connect(socket_b1, tenant_id="tenant-b", user_id=3)  # type: ignore[arg-type]

    await broadcaster.broadcast(
        telemetry_event("operational.a1", {"record": "a1"}, tenant_id="tenant-a", owner_user_id=1)
    )
    await broadcaster.broadcast(
        telemetry_event("operational.a2", {"record": "a2"}, tenant_id="tenant-a", owner_user_id=2)
    )
    await broadcaster.broadcast(
        telemetry_event("operational.b1", {"record": "b1"}, tenant_id="tenant-b", owner_user_id=3)
    )
    await asyncio.sleep(0.02)
    await broadcaster.stop()

    def event_types(socket: _FakeWebSocket) -> set[str]:
        return {
            event["type"]
            for frame in socket.events
            for event in frame["payload"]["events"]
        }

    assert event_types(socket_a1) == {"operational.a1"}
    assert event_types(socket_a2) == {"operational.a2"}
    assert event_types(socket_b1) == {"operational.b1"}

    admin_scope = TenantContext(
        tenant_id="tenant-a",
        user_id=1,
        membership_id="tenant-a:1",
        membership_role="admin",
        membership_status="active",
        user_status="active",
        tenant_status="active",
        user=SimpleNamespace(id=1),
    ).data_scope
    viewer_scope = TenantContext(
        tenant_id="tenant-a",
        user_id=1,
        membership_id="tenant-a:1",
        membership_role="viewer",
        membership_status="active",
        user_status="active",
        tenant_status="active",
        user=SimpleNamespace(id=1),
    ).data_scope
    assert admin_scope == viewer_scope == DataScope("tenant-a", 1)
