import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from backend.app.main import app
from backend.app.core.dependencies import get_redis_client
from backend.app.core.settings import get_ingestion_security_settings

client = TestClient(app)

VALID_KEY = "test-ingest-key"

def auth_headers(api_key: str = VALID_KEY) -> dict[str, str]:
    return {"X-API-Key": api_key}

@pytest.fixture(autouse=True)
def setup_mocks():
    # Mock Redis client
    mock_redis = MagicMock()
    mock_pipeline = MagicMock()
    mock_redis.pipeline.return_value = mock_pipeline
    
    from unittest.mock import AsyncMock
    mock_pipeline.execute = AsyncMock(return_value=[1, 1])
    
    app.dependency_overrides[get_redis_client] = lambda: mock_redis
    yield
    app.dependency_overrides.clear()

def test_ingest_logs_rejects_without_auth():
    with patch.dict("os.environ", {"INGEST_API_KEY": VALID_KEY}, clear=False):
        response = client.post("/v1/logs", json={})
    assert response.status_code == 401

def test_ingest_logs_accepts_valid_json():
    payload = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "test-service"}}
                    ]
                },
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "body": {"stringValue": "test log"},
                                "severityNumber": 9
                            }
                        ]
                    }
                ]
            }
        ]
    }
    with patch.dict("os.environ", {"INGEST_API_KEY": VALID_KEY}, clear=False):
        response = client.post("/v1/logs", json=payload, headers=auth_headers())
    assert response.status_code == 200

def test_ingest_logs_accepts_protobuf():
    try:
        from opentelemetry.proto.logs.v1.logs_pb2 import LogsData, ResourceLogs, ScopeLogs, LogRecord
        from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
        from opentelemetry.proto.resource.v1.resource_pb2 import Resource
    except ImportError:
        pytest.skip("opentelemetry-proto not installed")

    logs_data = LogsData()
    resource_logs = logs_data.resource_logs.add()
    resource = Resource()
    attr = resource.attributes.add()
    attr.key = "service.name"
    attr.value.string_value = "proto-service"
    resource_logs.resource.CopyFrom(resource)

    scope_logs = resource_logs.scope_logs.add()
    log_record = scope_logs.log_records.add()
    log_record.body.string_value = "test proto log"
    log_record.severity_number = 9

    pb_bytes = logs_data.SerializeToString()

    headers = auth_headers()
    headers["Content-Type"] = "application/x-protobuf"
    
    with patch.dict("os.environ", {"INGEST_API_KEY": VALID_KEY}, clear=False):
        response = client.post("/v1/logs", content=pb_bytes, headers=headers)
    assert response.status_code == 200

def test_ingest_logs_accepts_protobuf_gzip():
    import gzip
    try:
        from opentelemetry.proto.logs.v1.logs_pb2 import LogsData, ResourceLogs, ScopeLogs, LogRecord
        from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
        from opentelemetry.proto.resource.v1.resource_pb2 import Resource
    except ImportError:
        pytest.skip("opentelemetry-proto not installed")

    logs_data = LogsData()
    resource_logs = logs_data.resource_logs.add()
    resource = Resource()
    attr = resource.attributes.add()
    attr.key = "service.name"
    attr.value.string_value = "proto-gzip-service"
    resource_logs.resource.CopyFrom(resource)

    scope_logs = resource_logs.scope_logs.add()
    log_record = scope_logs.log_records.add()
    log_record.body.string_value = "test proto gzip log"
    log_record.severity_number = 9

    pb_bytes = logs_data.SerializeToString()
    compressed_bytes = gzip.compress(pb_bytes)

    headers = auth_headers()
    headers["Content-Type"] = "application/x-protobuf"
    headers["Content-Encoding"] = "gzip"
    
    with patch.dict("os.environ", {"INGEST_API_KEY": VALID_KEY}, clear=False):
        response = client.post("/v1/logs", content=compressed_bytes, headers=headers)
    assert response.status_code == 200
