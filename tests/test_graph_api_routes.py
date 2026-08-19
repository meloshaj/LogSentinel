from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.security.auth import get_current_user
from tests.test_blast_radius_persistence import populated_result


BASE_TIME = datetime(2026, 7, 24, 10, 0, tzinfo=timezone.utc)
VALID_KEY = "test-graph-key"


def _make_fake_user() -> MagicMock:
    """Return a lightweight mock that satisfies the get_current_user dependency."""
    user = MagicMock()
    user.id = 1
    user.email = "test@logsentinel.io"
    return user


@pytest.fixture(autouse=True)
def _override_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Override JWT auth for all tests in this module."""
    app.dependency_overrides[get_current_user] = _make_fake_user
    monkeypatch.setenv("INGEST_API_KEY", VALID_KEY)
    yield
    app.dependency_overrides.pop(get_current_user, None)


class FakeTopologyPipeline:
    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def get_snapshot(self) -> dict[str, Any]:
        self.calls += 1
        return self.snapshot


class FakeTrackingRepository:
    def __init__(self, rows: dict[int, dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[int] = []

    async def get_tracking_loop_by_id(self, tracking_loop_id: int) -> dict[str, Any] | None:
        self.calls.append(tracking_loop_id)
        return self.rows.get(tracking_loop_id)


def test_topology_endpoint_returns_populated_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = {
        "generated_at": "2026-07-24T10:00:00Z",
        "node_count": 2,
        "edge_count": 1,
        "transaction_count": 1,
        "nodes": [
            {"id": "svc_api_gateway", "name": "API Gateway", "type": "gateway", "status": "healthy", "metrics": {"throughput_rps": 100.0, "latency_p95_ms": 10.0, "error_rate_pct": 0.0}},
            {"id": "svc_inventory_db", "name": "Inventory DB", "type": "database", "status": "healthy", "metrics": {"throughput_rps": 50.0, "latency_p95_ms": 5.0, "error_rate_pct": 0.0}},
        ],
        "edges": [
            {
                "id": "edge_api_gateway_to_inventory_db",
                "source": "svc_api_gateway",
                "target": "svc_inventory_db",
                "call_count": 3,
                "avg_latency_ms": 12.5,
                "error_count": 0,
            }
        ],
    }
    fake = FakeTopologyPipeline(snapshot)
    monkeypatch.setattr("backend.app.main.topology_pipeline", fake)

    response = TestClient(app).get("/api/v1/topology")

    assert response.status_code == 200
    body = response.json()
    
    nodes = sorted(body["nodes"], key=lambda x: x["id"])
    edges = sorted(body["edges"], key=lambda x: (x["source"], x["target"]))
    
    assert nodes[0]["id"] == "svc_api_gateway"
    assert nodes[1]["id"] == "svc_inventory_db"
    assert edges[0]["source"] == "svc_api_gateway"
    assert edges[0]["target"] == "svc_inventory_db"
    assert fake.calls == 1


def test_topology_endpoint_returns_empty_topology(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.app.main.topology_pipeline",
        FakeTopologyPipeline(
            {
                "generated_at": None,
                "node_count": 0,
                "edge_count": 0,
                "transaction_count": 0,
                "nodes": [],
                "edges": [],
            }
        ),
    )

    response = TestClient(app).get("/api/v1/topology")

    assert response.status_code == 200
    assert response.json()["nodes"] == []
    assert response.json()["edges"] == []


def test_topology_endpoint_rejects_unauthenticated_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a valid JWT the endpoint must reject the request."""
    # Remove the auth override so the real dependency runs
    app.dependency_overrides.pop(get_current_user, None)

    monkeypatch.setattr(
        "backend.app.main.topology_pipeline",
        FakeTopologyPipeline(
            {
                "generated_at": None,
                "node_count": 0,
                "edge_count": 0,
                "transaction_count": 0,
                "nodes": [],
                "edges": [],
            }
        ),
    )

    response = TestClient(app).get("/api/v1/topology")

    assert response.status_code == 401  # HTTPBearer rejects missing credentials


def test_blast_radius_endpoint_returns_valid_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = populated_result().model_dump(mode="json")
    repository = FakeTrackingRepository(
        {
            42: {
                "id": 42,
                "window_id": "window-1",
                "anomaly_score": 0.8,
                "status": "triggered",
                "blast_radius": payload,
                "created_at": BASE_TIME,
            }
        }
    )
    monkeypatch.setattr("backend.app.main.tracking_repository", repository)

    response = TestClient(app).get(
        "/api/v1/tracking-loops/42/blast-radius",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["tracking_loop_id"] == 42
    assert body["analysis_available"] is True
    assert body["blast_radius"] == payload
    assert body["suspected_root_service"] == "db"
    assert body["root_cause_confidence"] == 0.73
    assert body["graph_analysis_version"] == "test-v1"
    assert "window_id" not in body
    assert repository.calls == [42]


def test_blast_radius_endpoint_missing_tracking_loop_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.app.main.tracking_repository", FakeTrackingRepository({}))

    response = TestClient(app).get(
        "/api/v1/tracking-loops/999/blast-radius",
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Tracking loop not found"


def test_blast_radius_endpoint_null_analysis_returns_available_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.app.main.tracking_repository",
        FakeTrackingRepository(
            {
                1: {
                    "id": 1,
                    "window_id": "window-1",
                    "blast_radius": None,
                    "created_at": BASE_TIME,
                }
            }
        ),
    )

    response = TestClient(app).get(
        "/api/v1/tracking-loops/1/blast-radius",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["analysis_available"] is False
    assert body["blast_radius"] is None


def test_blast_radius_endpoint_malformed_analysis_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.app.main.tracking_repository",
        FakeTrackingRepository(
            {
                1: {
                    "id": 1,
                    "window_id": "window-1",
                    "blast_radius": {"not": "a valid result"},
                    "created_at": BASE_TIME,
                }
            }
        ),
    )

    response = TestClient(app).get(
        "/api/v1/tracking-loops/1/blast-radius",
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Stored blast-radius analysis is malformed"


def test_blast_radius_endpoint_rejects_invalid_identifier() -> None:

    response = TestClient(app).get(
        "/api/v1/tracking-loops/not-an-int/blast-radius",
    )

    assert response.status_code == 422


def test_blast_radius_endpoint_rejects_unauthenticated_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a valid JWT the endpoint must reject the request."""
    # Remove the auth override so the real dependency runs
    app.dependency_overrides.pop(get_current_user, None)

    monkeypatch.setattr("backend.app.main.tracking_repository", FakeTrackingRepository({}))

    response = TestClient(app).get("/api/v1/tracking-loops/42/blast-radius")

    assert response.status_code == 401  # HTTPBearer rejects missing credentials
