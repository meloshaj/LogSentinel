import os
import pytest
import redis
from backend.app.services.drain_parser import DrainParser

@pytest.fixture
def redis_sync_client():
    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", "6379"))
    
    client = redis.Redis(host=host, port=port)
    try:
        client.ping()
    except Exception:
        pytest.skip(f"Redis not available on {host}:{port}")
    
    # Clean state before test
    client.delete("logsentinel:drain3:state")
    
    yield client
    
    # Clean state after test
    client.delete("logsentinel:drain3:state")
    client.close()

def test_drain_parser_redis_state_sharing(redis_sync_client):
    # 1. Instantiate first parser
    parser_a = DrainParser()
    
    # Process a log message
    log_1 = "[ERROR] Connection lost to DB 192.168.1.1"
    result_a = parser_a.parse(log_1)
    
    template_id_a = result_a.template_id
    assert template_id_a is not None
    assert "Connection lost to DB" in result_a.template_text
    
    # Drain3's TemplateMiner automatically saves state on new cluster creation.
    # Check that the state exists in Redis
    state_data = redis_sync_client.get("logsentinel:drain3:state")
    assert state_data is not None
    
    # 2. Instantiate second parser (simulating a new worker node starting up)
    parser_b = DrainParser()
    
    # Process a structurally identical log message
    log_2 = "[ERROR] Connection lost to DB 10.0.0.5"
    result_b = parser_b.parse(log_2)
    
    template_id_b = result_b.template_id
    assert template_id_b is not None
    
    # The second parser should have loaded the state from Redis, meaning it 
    # matched the existing cluster instead of creating a new one.
    assert template_id_a == template_id_b
    assert result_a.template_text == result_b.template_text
    assert result_b.cluster_size == 2
