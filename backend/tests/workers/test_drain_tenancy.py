import pytest
from backend.app.workers.drain_worker import DrainWorker

def test_drain_worker_extracts_tenant_id_from_payload():
    """Verify that DrainWorker extracts tenant_id from the root stream payload."""
    class DummyBatchManager:
        pass
        
    class DummyParser:
        pass

    worker = DrainWorker(
        batch_manager=DummyBatchManager(),
        parser=DummyParser(),
        log_buffer=None,
    )
    
    mock_payload = {
        "tenant_id": "tenant-test-123",
        "source": "api-gateway",
        "correlation_id": "corr-456",
        "logs": [
            {
                "raw_message": "test log 1",
                "service": "auth"
            }
        ]
    }
    
    extracted = worker._extract_log_messages(mock_payload)
    
    assert len(extracted) == 1
    raw, metadata = extracted[0]
    
    assert raw == "test log 1"
    assert metadata.get("tenant_id") == "tenant-test-123"
    assert metadata.get("source") == "api-gateway"
    assert metadata.get("service") == "auth"
    assert metadata.get("correlation_id") == "corr-456"

def test_drain_worker_extract_entry_allows_tenant_id_override():
    """Verify that tenant_id in an individual entry overrides the parent payload."""
    class DummyBatchManager: pass
    class DummyParser: pass

    worker = DrainWorker(
        batch_manager=DummyBatchManager(),
        parser=DummyParser(),
        log_buffer=None,
    )
    
    mock_payload = {
        "tenant_id": "tenant-parent",
        "logs": [
            {
                "raw_message": "test log 2",
                "tenant_id": "tenant-child",
            }
        ]
    }
    
    extracted = worker._extract_log_messages(mock_payload)
    assert len(extracted) == 1
    raw, metadata = extracted[0]
    
    assert metadata.get("tenant_id") == "tenant-child"
