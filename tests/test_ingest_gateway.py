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

    class FakeBatchManager:
        async def flush_all(self) -> None:
            events.append("batch.shutdown_flush")

    class FakeDrainWorker:
        def set_redis_client(self, *args, **kwargs) -> None: pass
        def start(self) -> None:
            events.append("drain.start")
        async def stop(self) -> None:
            events.append("drain.stop")

    class FakeFeatureWorker:
        def start(self) -> None:
            events.append("feature.start")
        async def stop(self) -> None:
            events.append("feature.stop")
            
    class FakeEventManager:
        def set_redis_client(self, *args, **kwargs): pass
        def start(self) -> None: pass
        async def stop(self) -> None: pass
        
    class FakeStreamCleaner:
        def set_redis_client(self, *args, **kwargs) -> None: pass
        def start(self) -> None: pass
        async def stop(self) -> None: pass

    class FakeTelemetryManager:
        def set_redis_client(self, *args, **kwargs) -> None: pass
        def start(self) -> None: pass
        async def stop(self) -> None: pass

    def fake_init_engine(_settings) -> None:
        events.append("database.init")

    async def fake_dispose_engine() -> None:
        events.append("database.dispose")
        
    async def fake_init_redis_pool() -> object:
        events.append("redis.init")
        return object()
        
    async def fake_close_redis_pool() -> None:
        events.append("redis.dispose")

    async def fake_verify_connectivity() -> None:
        pass

    async def fake_verify_schema_ready() -> None:
        events.append("database.schema")

    async def fake_ensure_stream_and_group(redis_client, stream_name, group_name) -> None:
        pass

    monkeypatch.setattr(main_module, "get_database_settings", lambda: object())
    monkeypatch.setattr(main_module, "init_engine", fake_init_engine)
    monkeypatch.setattr(main_module, "get_engine", lambda: FakeEngine())
    monkeypatch.setattr(main_module, "dispose_engine", fake_dispose_engine)
    monkeypatch.setattr(main_module, "verify_connectivity", fake_verify_connectivity)
    monkeypatch.setattr(main_module, "verify_schema_ready", fake_verify_schema_ready)
    monkeypatch.setattr(main_module, "ensure_stream_and_group", fake_ensure_stream_and_group)
    monkeypatch.setattr(main_module, "drain_worker", FakeDrainWorker())
    monkeypatch.setattr(main_module, "feature_worker", FakeFeatureWorker())
    monkeypatch.setattr(main_module, "event_manager", FakeEventManager())
    monkeypatch.setattr(main_module, "stream_cleaner", FakeStreamCleaner())
    monkeypatch.setattr(main_module, "telemetry_manager", FakeTelemetryManager())
    monkeypatch.setattr(main_module, "batch_manager", FakeBatchManager())
    monkeypatch.setattr(main_module, "init_redis_pool", fake_init_redis_pool)
    monkeypatch.setattr(main_module, "close_redis_pool", fake_close_redis_pool)

    async def run_lifespan() -> None:
        async with main_module.lifespan(main_module.app):
            pass

    asyncio.run(run_lifespan())

    assert events[-4:] == [
        "feature.stop",
        "batch.shutdown_flush",
        "database.dispose",
        "redis.dispose",
    ]


class MockRedisPipeline:
    def xadd(self, *args, **kwargs): pass
    def xlen(self, *args, **kwargs): return 0
    async def execute(self): return [None, 0]

class MockRedis:
    def pipeline(self, transaction=False):
        return MockRedisPipeline()

@pytest.fixture(autouse=True)
def mock_redis_state(monkeypatch):
    monkeypatch.setattr(main_module.app.state, "redis", MockRedis(), raising=False)
    yield


def test_ingest_log_returns_202_for_valid_payload() -> None:
    with patch.dict("os.environ", {"INGEST_API_KEY": VALID_KEY}, clear=False):
        response = client.post("/ingest-log", json=valid_payload(), headers=auth_headers())

    assert response.status_code == 202
    body = response.json()
    assert body["message"] == "Payload accepted"
    assert body["accepted"] is True
    assert body["queue_size"] >= 0


def test_ingest_log_rejects_missing_logs(monkeypatch) -> None:
    monkeypatch.setattr(main_module.app.state, "redis", MockRedis(), raising=False)
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


def test_ingest_log_rejects_missing_api_key(monkeypatch) -> None:
    monkeypatch.setattr(main_module.app.state, "redis", MockRedis(), raising=False)
    with patch.dict("os.environ", {"INGEST_API_KEY": VALID_KEY}, clear=False):
        response = client.post("/ingest-log", json=valid_payload())

    assert response.status_code == 401
    assert response.json() == {"detail": "missing_api_key"}
    assert VALID_KEY not in response.text


def test_ingest_log_rejects_invalid_api_key(monkeypatch) -> None:
    monkeypatch.setattr(main_module.app.state, "redis", MockRedis(), raising=False)
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


def test_unauthorized_ingest_does_not_enqueue_logs(monkeypatch) -> None:
    calls = []
    class MockRedisPipeline:
        def xadd(self, *args, **kwargs):
            calls.append("xadd")
        def xlen(self, *args, **kwargs):
            return 0
        async def execute(self):
            return [None, 0]

    class MockRedis:
        def pipeline(self, transaction=False):
            return MockRedisPipeline()

    monkeypatch.setattr(main_module.app.state, "redis", MockRedis(), raising=False)

    with patch.dict("os.environ", {"INGEST_API_KEY": VALID_KEY}, clear=False):
        response = client.post("/ingest-log", json=valid_payload(), headers=auth_headers("wrong-key"))

    assert response.status_code == 403
    assert len(calls) == 0


def test_ingest_log_preserves_queue_full_response(monkeypatch) -> None:
    class MockRedisPipeline:
        def xadd(self, *args, **kwargs):
            pass
        def xlen(self, *args, **kwargs):
            pass
        async def execute(self):
            raise RuntimeError("Simulated Redis failure")

    class MockRedis:
        def pipeline(self, transaction=False):
            return MockRedisPipeline()

    monkeypatch.setattr(main_module.app.state, "redis", MockRedis(), raising=False)

    with patch.dict("os.environ", {"INGEST_API_KEY": VALID_KEY}, clear=False):
        response = client.post("/ingest-log", json=valid_payload(), headers=auth_headers())

    assert response.status_code == 503
    body = response.json()
    assert body["accepted"] is False
    assert body["queue_size"] == 0
