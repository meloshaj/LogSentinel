import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import backend.app.main as main_module
from backend.app.main import app

client = TestClient(app)

VALID_KEY = "test-ingest-key"


def auth_headers(api_key: str = VALID_KEY) -> dict[str, str]:
    return {"X-API-Key": api_key}


def valid_payload() -> dict:
    return {
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
    }


def test_async_log_buffer_join_waits_for_exactly_one_task_done() -> None:
    async def run() -> None:
        log_buffer = main_module.AsyncLogBuffer(maxsize=1)
        payload = {"logs": [{"message": "queued"}]}
        assert log_buffer.enqueue(payload)
        assert await log_buffer.dequeue() == payload

        join_task = asyncio.create_task(log_buffer.join())
        await asyncio.sleep(0)
        assert join_task.done() is False

        log_buffer.task_done()
        await asyncio.wait_for(join_task, timeout=0.1)

        with pytest.raises(ValueError):
            log_buffer.task_done()

    asyncio.run(run())


def test_lifespan_stops_drain_before_features_and_database(monkeypatch) -> None:
    events: list[str] = []

    class FakeConnection:
        async def run_sync(self, _operation) -> None:
            events.append("database.schema")

    class FakeBeginContext:
        async def __aenter__(self) -> FakeConnection:
            return FakeConnection()

        async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
            return None

    class FakeEngine:
        def begin(self) -> FakeBeginContext:
            return FakeBeginContext()

    class FakeDrainWorker:
        def start(self) -> None:
            events.append("drain.start")

        async def stop(self) -> None:
            events.append("drain.stop")
            events.append("batch.shutdown_flush")

    class FakeFeatureWorker:
        def start(self) -> None:
            events.append("feature.start")

        async def stop(self) -> None:
            events.append("feature.stop")

    def fake_init_engine(_settings) -> None:
        events.append("database.init")

    async def fake_dispose_engine() -> None:
        events.append("database.dispose")

    monkeypatch.setattr(main_module, "get_database_settings", lambda: object())
    monkeypatch.setattr(main_module, "init_engine", fake_init_engine)
    monkeypatch.setattr(main_module, "get_engine", lambda: FakeEngine())
    monkeypatch.setattr(main_module, "dispose_engine", fake_dispose_engine)
    monkeypatch.setattr(main_module, "drain_worker", FakeDrainWorker())
    monkeypatch.setattr(main_module, "feature_worker", FakeFeatureWorker())

    async def run_lifespan() -> None:
        async with main_module.lifespan(main_module.app):
            pass

    asyncio.run(run_lifespan())

    assert events[-4:] == [
        "drain.stop",
        "batch.shutdown_flush",
        "feature.stop",
        "database.dispose",
    ]


def test_ingest_log_returns_202_for_valid_payload() -> None:
    with patch.dict("os.environ", {"INGEST_API_KEY": VALID_KEY}, clear=False):
        response = client.post("/ingest-log", json=valid_payload(), headers=auth_headers())

    assert response.status_code == 202
    body = response.json()
    assert body["message"] == "Log payload accepted for asynchronous processing"
    assert body["accepted"] is True
    assert body["queue_size"] >= 0


def test_ingest_log_rejects_missing_logs() -> None:
    with patch.dict("os.environ", {"INGEST_API_KEY": VALID_KEY}, clear=False):
        response = client.post(
            "/ingest-log",
            json={
                "source": "api-gateway",
                "environment": "dev",
            },
            headers=auth_headers(),
        )

    assert response.status_code == 422


def test_ingest_log_rejects_missing_api_key() -> None:
    with patch.dict("os.environ", {"INGEST_API_KEY": VALID_KEY}, clear=False):
        response = client.post("/ingest-log", json=valid_payload())

    assert response.status_code == 401
    assert response.json() == {"detail": "missing_api_key"}
    assert VALID_KEY not in response.text


def test_ingest_log_rejects_invalid_api_key() -> None:
    with patch.dict("os.environ", {"INGEST_API_KEY": VALID_KEY}, clear=False):
        response = client.post("/ingest-log", json=valid_payload(), headers=auth_headers("wrong-key"))

    assert response.status_code == 403
    assert response.json() == {"detail": "invalid_api_key"}
    assert VALID_KEY not in response.text


def test_ingest_log_accepts_key_from_ingest_api_keys() -> None:
    with patch.dict("os.environ", {"INGEST_API_KEYS": "first-key, second-key"}, clear=False):
        response = client.post("/ingest-log", json=valid_payload(), headers=auth_headers("second-key"))

    assert response.status_code == 202
    assert response.json()["accepted"] is True


def test_ingest_log_rejects_when_guard_is_not_configured() -> None:
    env = {"INGEST_API_KEY": "", "INGEST_API_KEYS": ""}
    with patch.dict("os.environ", env, clear=False):
        response = client.post("/ingest-log", json=valid_payload(), headers=auth_headers())

    assert response.status_code == 503
    assert response.json() == {"detail": "ingestion_guard_not_configured"}


def test_unauthorized_ingest_does_not_enqueue_logs() -> None:
    before = main_module.get_log_buffer().queue_size()

    with patch.dict("os.environ", {"INGEST_API_KEY": VALID_KEY}, clear=False):
        response = client.post("/ingest-log", json=valid_payload(), headers=auth_headers("wrong-key"))

    after = main_module.get_log_buffer().queue_size()
    assert response.status_code == 403
    assert after == before


def test_ingest_log_preserves_queue_full_response(monkeypatch) -> None:
    class FullBuffer:
        def enqueue(self, payload: dict) -> bool:
            return False

        def queue_size(self) -> int:
            return 10000

    monkeypatch.setattr(main_module, "get_log_buffer", lambda: FullBuffer())

    with patch.dict("os.environ", {"INGEST_API_KEY": VALID_KEY}, clear=False):
        response = client.post("/ingest-log", json=valid_payload(), headers=auth_headers())

    assert response.status_code == 503
    body = response.json()
    assert body["accepted"] is False
    assert body["queue_size"] == 10000


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


def test_drain3_db_health_returns_status_payload(monkeypatch) -> None:
    async def fake_check_database_health() -> dict:
        return {
            "connected": False,
            "table_exists": False,
            "missing_columns": [],
            "error": None,
        }

    monkeypatch.setattr(main_module, "check_database_health", fake_check_database_health)

    response = client.get("/drain3/db-health")

    assert response.status_code == 200
    body = response.json()
    assert "connected" in body
    assert "table_exists" in body
    assert "missing_columns" in body
    assert "error" in body
