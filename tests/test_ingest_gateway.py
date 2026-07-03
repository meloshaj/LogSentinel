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


def test_drain3_stats_returns_parser_and_worker_stats() -> None:
    response = client.get("/drain3/stats")

    assert response.status_code == 200
    body = response.json()
    assert "parser" in body
    assert "worker" in body
    assert "batch" in body
    assert "cluster_count" in body["parser"]
    assert "processed_count" in body["worker"]
    assert "queue_size" in body["worker"]
    assert "current_buffer_size" in body["batch"]
    assert "periodic_flush_enabled" in body["batch"]
    assert "flush_interval_seconds" in body["batch"]
    assert "periodic_flush_count" in body["batch"]
    assert "shutdown_flush_count" in body["batch"]
    assert "failed_batch_count" in body["batch"]


def test_drain3_flush_returns_batch_stats() -> None:
    response = client.post("/drain3/flush")

    assert response.status_code == 200
    body = response.json()
    assert "batch" in body
    assert "batch_size" in body["batch"]
    assert "current_buffer_size" in body["batch"]
    assert "last_sink_result" in body["batch"]
    assert "last_sink_error" in body["batch"]


def test_drain3_db_health_returns_status_payload() -> None:
    response = client.get("/drain3/db-health")

    assert response.status_code == 200
    body = response.json()
    assert "connected" in body
    assert "table_exists" in body
    assert "missing_columns" in body
    assert "error" in body
