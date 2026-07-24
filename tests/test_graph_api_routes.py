from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from backend.app.main import app
from tests.test_blast_radius_persistence import populated_result


BASE_TIME = datetime(2026, 7, 24, 10, 0, tzinfo=timezone.utc)
VALID_KEY = "test-graph-key"


def auth_headers(api_key: str = VALID_KEY) -> dict[str, str]:
    return {"X-API-Key": api_key}


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


def test_topology_endpoint_returns_populated_snapshot(monkeypatch) -> None:
    snapshot = {
        "generated_at": "2026-07-24T10:00:00Z",
        "node_count": 2,
        "edge_count": 1,
        "transaction_count": 1,
        "nodes": [
            {"id": "api-gateway", "service": "api-gateway"},
            {"id": "inventory-db", "service": "inventory-db"},
        ],
        "edges": [
            {
                "id": "api-gateway->inventory-db",
                "source": "api-gateway",
                "target": "inventory-db",
                "transition_count": 3,
                "average_delay_ms": 12.5,
            }
        ],
    }
    fake = FakeTopologyPipeline(snapshot)
    monkeypatch.setattr("backend.app.main.topology_pipeline", fake)

    monkeypatch.setenv("INGEST_API_KEY", VALID_KEY)

    response = TestClient(app).get("/api/v1/topology", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["direction"] == "caller_to_callee"
    assert body["node_count"] == 2
    assert body["edge_count"] == 1
    assert body["nodes"][0]["id"] == "api-gateway"
    assert body["edges"][0]["source"] == "api-gateway"
    assert body["edges"][0]["target"] == "inventory-db"
    assert fake.calls == 1


def test_topology_endpoint_returns_empty_topology(monkeypatch) -> None:
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

    monkeypatch.setenv("INGEST_API_KEY", VALID_KEY)

    response = TestClient(app).get("/api/v1/topology", headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["nodes"] == []
    assert response.json()["edges"] == []


def test_topology_endpoint_rejects_missing_api_key(monkeypatch) -> None:
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
    monkeypatch.setenv("INGEST_API_KEY", VALID_KEY)

    response = TestClient(app).get("/api/v1/topology")

    assert response.status_code == 401
    assert response.json()["detail"] == "missing_api_key"


def test_blast_radius_endpoint_returns_valid_analysis(monkeypatch) -> None:
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
    monkeypatch.setenv("INGEST_API_KEY", VALID_KEY)

    response = TestClient(app).get(
        "/api/v1/tracking-loops/42/blast-radius",
        headers=auth_headers(),
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


def test_blast_radius_endpoint_missing_tracking_loop_returns_404(monkeypatch) -> None:
    monkeypatch.setattr("backend.app.main.tracking_repository", FakeTrackingRepository({}))
    monkeypatch.setenv("INGEST_API_KEY", VALID_KEY)

    response = TestClient(app).get(
        "/api/v1/tracking-loops/999/blast-radius",
        headers=auth_headers(),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Tracking loop not found"


def test_blast_radius_endpoint_null_analysis_returns_available_false(monkeypatch) -> None:
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
    monkeypatch.setenv("INGEST_API_KEY", VALID_KEY)

    response = TestClient(app).get(
        "/api/v1/tracking-loops/1/blast-radius",
        headers=auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["analysis_available"] is False
    assert body["blast_radius"] is None


def test_blast_radius_endpoint_malformed_analysis_is_safe(monkeypatch) -> None:
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
    monkeypatch.setenv("INGEST_API_KEY", VALID_KEY)

    response = TestClient(app).get(
        "/api/v1/tracking-loops/1/blast-radius",
        headers=auth_headers(),
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Stored blast-radius analysis is malformed"


def test_blast_radius_endpoint_rejects_invalid_identifier(monkeypatch) -> None:
    monkeypatch.setenv("INGEST_API_KEY", VALID_KEY)

    response = TestClient(app).get(
        "/api/v1/tracking-loops/not-an-int/blast-radius",
        headers=auth_headers(),
    )

    assert response.status_code == 422


def test_blast_radius_endpoint_rejects_missing_api_key(monkeypatch) -> None:
    monkeypatch.setattr("backend.app.main.tracking_repository", FakeTrackingRepository({}))
    monkeypatch.setenv("INGEST_API_KEY", VALID_KEY)

    response = TestClient(app).get("/api/v1/tracking-loops/42/blast-radius")

    assert response.status_code == 401
    assert response.json()["detail"] == "missing_api_key"
