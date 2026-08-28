import asyncio
import json
import pytest
from redis.asyncio import Redis

from backend.app.workers.drain_worker import DrainWorker
from backend.app.services.drain_parser import DrainParser
from backend.app.services.batch_manager import ParsedLogBatchManager

import pytest_asyncio

@pytest_asyncio.fixture
async def redis_client():
    client = Redis.from_url("redis://localhost:6379/0", decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip("Redis not available on localhost:6379")
    
    yield client
    
    # Cleanup after test
    await client.delete("logs:test_stream")
    await client.aclose()

@pytest.mark.asyncio
async def test_pel_auto_recovery_and_processing(redis_client: Redis, tmp_path):
    stream_name = "logs:test_stream"
    group_name = "log_workers"
    
    # Ensure clean state
    await redis_client.delete(stream_name)
    
    # 1. Create stream and group
    try:
        await redis_client.xgroup_create(stream_name, group_name, id="$", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise
        
    # 2. Add some logs
    payload = {
        "source": "test-resilience",
        "environment": "test",
        "logs": [{"service_name": "test-service", "message": "Test recovery message"}]
    }
    
    # Add to stream
    msg_id = await redis_client.xadd(stream_name, {"payload": json.dumps(payload)})
    
    # 3. Simulate a crash: XREADGROUP without XACK
    crashed_worker = "crashed_worker_123"
    messages = await redis_client.xreadgroup(
        groupname=group_name,
        consumername=crashed_worker,
        streams={stream_name: ">"},
        count=10,
        block=100
    )
    
    assert len(messages) > 0
    assert messages[0][1][0][0] == msg_id
    
    # Verify it is in PEL
    pending = await redis_client.xpending(stream_name, group_name)
    assert pending["pending"] == 1
    
    # 4. Initialize DrainWorker with short recovery idle time
    from drain3.file_persistence import FilePersistence
    pers = FilePersistence(str(tmp_path / "redis_drain_state.bin"))
    parser = DrainParser(persistence=pers)
    batch_manager = ParsedLogBatchManager()
    
    worker = DrainWorker(
        log_buffer=None,
        parser=parser,
        batch_manager=batch_manager
    )
    worker.set_redis_client(redis_client)
    worker.stream_name = stream_name
    worker.group_name = group_name
    worker.recovery_idle_time_ms = 1  # 1ms to claim immediately
    worker._running = True  # Mock running state to allow loop execution
    
    # 5. Trigger XAUTOCLAIM manually for the test
    # (In production, this is called inside recover_pending_messages loop)
    min_idle_ms = worker.recovery_idle_time_ms
    result = await worker.redis_client.xautoclaim(
        name=worker.stream_name,
        groupname=worker.group_name,
        consumername=worker.consumer_name,
        min_idle_time=min_idle_ms,
        start_id="0-0",
        count=100
    )
    
    claimed_messages = result[1]
    assert len(claimed_messages) == 1
    
    claimed_msg_id = claimed_messages[0][0]
    entry = claimed_messages[0][1]
    
    assert claimed_msg_id == msg_id
    
    # Process it directly as the worker would
    payload_json = entry.get(b"payload") or entry.get("payload")
    if isinstance(payload_json, bytes):
        payload_json = payload_json.decode("utf-8")
    
    decoded_payload = json.loads(payload_json)
    
    await worker.process_one(decoded_payload)
    
    # Send XACK on success
    await worker.redis_client.xack(worker.stream_name, worker.group_name, claimed_msg_id)
    
    # 6. Verify PEL is empty
    pending_after = await redis_client.xpending(stream_name, group_name)
    assert pending_after["pending"] == 0
