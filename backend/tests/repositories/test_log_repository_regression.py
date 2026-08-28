import os
from datetime import datetime, timedelta, timezone

import pytest
from backend.app.repositories.log_repository import LogRepository
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_get_recent_correlation_evidence_unmocked():
    """Test get_recent_correlation_evidence directly against DB."""
    raw_url = os.getenv("LOGSENTINEL_DB_REMEDIATION_TEST_URL")
    if not raw_url or os.getenv("LOGSENTINEL_ALLOW_DISPOSABLE_SCHEMA_TEST") != "1":
        pytest.skip("set disposable TimescaleDB test URL and explicit opt-in")

    engine = create_async_engine(raw_url)
    repo = LogRepository(engine=engine)

    async with engine.begin() as conn:
        now = datetime.now(timezone.utc)
        stmt = text("""
            INSERT INTO logs (id, timestamp, service, raw_message, template_id, level, correlation_id, metadata, created_at)
            VALUES 
            ('test_log_1', :now, 'service-a', 'msg1', 'tmpl1', 'INFO', 'corr-1', '{"key": "val1"}', :now),
            ('test_log_2', :now, 'service-a', 'msg2', 'tmpl1', 'ERROR', 'corr-1', '{"key": "val2"}', :now)
        """)
        await conn.execute(stmt, {"now": now})

    start_time = now - timedelta(minutes=5)
    end_time = now + timedelta(minutes=5)

    logs = await repo.get_recent_correlation_evidence(
        start_time=start_time, end_time=end_time, correlation_ids=["corr-1"]
    )

    assert len(logs) >= 2
    found_logs = [log for log in logs if log["id"] in ("test_log_1", "test_log_2")]
    assert len(found_logs) == 2
    for log in found_logs:
        assert "metadata" in log  # Ensure metadata is fetched correctly
        assert log["correlation_id"] == "corr-1"

    await engine.dispose()


@pytest.mark.asyncio
async def test_get_recent_logs_unmocked():
    """Test get_recent_logs directly against DB."""
    raw_url = os.getenv("LOGSENTINEL_DB_REMEDIATION_TEST_URL")
    if not raw_url or os.getenv("LOGSENTINEL_ALLOW_DISPOSABLE_SCHEMA_TEST") != "1":
        pytest.skip("set disposable TimescaleDB test URL and explicit opt-in")

    engine = create_async_engine(raw_url)
    repo = LogRepository(engine=engine)

    async with engine.begin() as conn:
        now = datetime.now(timezone.utc)
        stmt = text("""
            INSERT INTO logs (id, timestamp, service, raw_message, template_id, level, metadata, created_at)
            VALUES 
            ('test_log_3', :now, 'service-b', 'msg3', 'tmpl2', 'INFO', '{"k": "v"}', :now)
        """)
        await conn.execute(stmt, {"now": now})

    logs = await repo.get_recent_logs(limit=10)

    found = next((log for log in logs if log["id"] == "test_log_3"), None)
    assert found is not None
    assert "metadata" in found

    await engine.dispose()
