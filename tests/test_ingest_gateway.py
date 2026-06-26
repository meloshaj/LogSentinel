from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_ingest_log_returns_202_for_valid_payload() -> None:
    response = client.post(
        "/ingest-log",
        json={
            "source": "api-gateway",
            "environment": "dev",
            "correlation_id": "abc-123",
            "logs": [
                {
                    "service_name": "orders",
                    "level": "info",
                    "message": "Order created",
                    "metadata": {"order_id": 42},
                }
            ],
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] is True
    assert body["queue_size"] >= 0


def test_ingest_log_rejects_missing_logs() -> None:
    response = client.post(
        "/ingest-log",
        json={
            "source": "api-gateway",
            "environment": "dev",
        },
    )

    assert response.status_code == 422
