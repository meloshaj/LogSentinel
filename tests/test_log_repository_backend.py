from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace

import pytest

from backend.app.models import ParsedLog
from backend.app.repositories.log_repository import LogRepository


@pytest.fixture
def mock_engine():
    engine = MagicMock()
    conn = AsyncMock()
    
    # Mock connection context manager
    engine.connect.return_value.__aenter__.return_value = conn
    
    # Mock raw connection for asyncpg
    raw_conn = MagicMock()
    asyncpg_conn = AsyncMock()
    raw_conn.driver_connection = asyncpg_conn
    conn.get_raw_connection.return_value = raw_conn
    async def execute(statement, *args, **kwargs):
        params = statement.compile().params
        indexes = sorted({key.rsplit("_m", 1)[1] for key in params if key.startswith("event_id_m")}, key=int)
        if indexes:
            return [SimpleNamespace(
                tenant_id=params[f"tenant_id_m{i}"],
                owner_user_id=params[f"owner_user_id_m{i}"],
                event_id=params[f"event_id_m{i}"],
            ) for i in indexes]
        return MagicMock()
    conn.execute.side_effect = execute
    
    return engine, conn, asyncpg_conn


@pytest.mark.asyncio
async def test_bulk_insert_live_logs_only(mock_engine):
    engine, conn, asyncpg_conn = mock_engine
    repo = LogRepository(engine=engine)
    
    # Create logs from today (live)
    now = datetime.now(timezone.utc)
    logs = [
        ParsedLog(
            tenant_id="test-tenant", owner_user_id=101,
            id=f"TEST_LIVE_{i}",
            timestamp=now,
            service="test_svc",
            raw_message="test message",
            template_id="T1",
            template_text="test message",
            level="INFO",
            parameters=[],
            metadata={},
        )
        for i in range(3)
    ]
    
    await repo.bulk_insert_parsed_logs(logs)
    
    # Verify copy_records_to_table was called once with 3 records
    asyncpg_conn.copy_records_to_table.assert_called_once()
    args, kwargs = asyncpg_conn.copy_records_to_table.call_args
    assert args[0] == "logs"
    assert len(kwargs["records"]) == 3
    
    # One ledger claim plus one durable feature-contribution row per event.
    assert conn.execute.call_count == 4
    # Verify commit was called
    conn.commit.assert_called_once()


@pytest.mark.asyncio
async def test_bulk_insert_mixed_logs(mock_engine):
    engine, conn, asyncpg_conn = mock_engine
    repo = LogRepository(engine=engine)
    
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(days=5)
    
    # 2 live logs, 2 late logs
    logs = [
        ParsedLog(
            tenant_id="test-tenant", owner_user_id=101,
            id="TEST_LIVE_1", timestamp=now, service="test", raw_message="msg", template_id="T1", level="INFO", parameters=[], metadata={}
        ),
        ParsedLog(
            tenant_id="test-tenant", owner_user_id=101,
            id="TEST_LIVE_2", timestamp=now, service="test", raw_message="msg", template_id="T1", level="INFO", parameters=[], metadata={}
        ),
        ParsedLog(
            tenant_id="test-tenant", owner_user_id=101,
            id="TEST_LATE_1", timestamp=old_time, service="test", raw_message="msg", template_id="T1", level="INFO", parameters=[], metadata={}
        ),
        ParsedLog(
            tenant_id="test-tenant", owner_user_id=101,
            id="TEST_LATE_2", timestamp=old_time, service="test", raw_message="msg", template_id="T1", level="INFO", parameters=[], metadata={}
        ),
    ]
    
    await repo.bulk_insert_parsed_logs(logs)
    
    # Verify copy_records_to_table was called for the 2 live logs
    asyncpg_conn.copy_records_to_table.assert_called_once()
    kwargs = asyncpg_conn.copy_records_to_table.call_args.kwargs
    assert len(kwargs["records"]) == 2
    # Ensure they are the live ones
    assert kwargs["records"][0][2] == "TEST_LIVE_1"
    assert kwargs["records"][1][2] == "TEST_LIVE_2"
    
    # Verify execute was called for the late logs
    # Ledger + one late-log insert + four idempotent feature registrations.
    assert conn.execute.call_count == 6
    
    # Verify commit
    conn.commit.assert_called_once()


@pytest.mark.asyncio
async def test_bulk_insert_late_logs_only(mock_engine):
    engine, conn, asyncpg_conn = mock_engine
    repo = LogRepository(engine=engine)
    
    old_time = datetime.now(timezone.utc) - timedelta(days=10)
    
    logs = [
        ParsedLog(
            tenant_id="test-tenant", owner_user_id=101,
            id="TEST_LATE_1", timestamp=old_time, service="test", raw_message="msg", template_id="T1", level="INFO", parameters=[], metadata={}
        )
    ]
    
    await repo.bulk_insert_parsed_logs(logs)
    
    # copy_records_to_table should NOT be called
    asyncpg_conn.copy_records_to_table.assert_not_called()
    
    # execute should be called
    assert conn.execute.call_count == 3
    conn.commit.assert_called_once()

def test_partition_log_batch_timezone_handling():
    repo = LogRepository()
    now = datetime.now(timezone.utc)
    
    iso_time_late = (now - timedelta(days=5)).isoformat().replace("+00:00", "Z")
    naive_time_live = (now - timedelta(hours=1)).replace(tzinfo=None)
    aware_time_late = now - timedelta(days=5)
    
    rows = [
        {"created_at": iso_time_late},
        {"created_at": naive_time_live},
        {"created_at": aware_time_late}
    ]
    
    live_logs, late_logs = repo._partition_log_batch(rows)
    assert len(live_logs) == 1
    assert len(late_logs) == 2


@pytest.mark.asyncio
async def test_late_logs_sub_batching(mock_engine):
    engine, conn, asyncpg_conn = mock_engine
    repo = LogRepository(engine=engine)
    
    old_time = datetime.now(timezone.utc) - timedelta(days=10)
    
    # 1200 late logs
    logs = [
        ParsedLog(
            tenant_id="test-tenant", owner_user_id=101,
            id=f"TEST_LATE_{i}", timestamp=old_time, service="test", raw_message="msg", template_id="T1", level="INFO", parameters=[], metadata={}
        )
        for i in range(1200)
    ]
    
    await repo.bulk_insert_parsed_logs(logs)
    
    # copy_records_to_table should NOT be called
    asyncpg_conn.copy_records_to_table.assert_not_called()
    
    # Ledger plus three late-log sub-batches and 1200 feature registrations.
    assert conn.execute.call_count == 1204
    conn.commit.assert_called_once()
