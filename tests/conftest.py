from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

# Mock redis.StrictRedis at import time to prevent Drain3 from hanging during pytest collection
class MockRedisImport:
    def ping(self): pass
    def get(self, *args, **kwargs): return None
    def set(self, *args, **kwargs): pass
patch("redis.StrictRedis", return_value=MockRedisImport()).start()

from backend.app.models import ParsedLog

@pytest.fixture
def make_parsed_log():
    def _make_parsed_log(**overrides) -> ParsedLog:
        default_data = {
            "id": str(uuid.uuid4()),
            "service": "auth-service",
            "level": "INFO",
            "raw_message": "User authenticated successfully",
            "template_id": "E12",
            "template_text": "User authenticated successfully",
            "created_at": datetime.now(timezone.utc),
            "timestamp": datetime.now(timezone.utc),
            "parameters": [{"value": "user_123", "mask_name": "ID"}],
            "correlation_id": None,
            "metadata": {}
        }
        default_data.update(overrides)
        return ParsedLog(**default_data)
    return _make_parsed_log

from unittest.mock import patch, AsyncMock

@pytest.fixture(autouse=True)
def mock_redis_globally():
    """Mock Redis initialization globally to prevent tests from trying to connect to a real Redis server
    during the FastAPI lifespan event."""
    
    class MockRedisPipeline:
        def xadd(self, *args, **kwargs): pass
        def xlen(self, *args, **kwargs): return 0
        async def execute(self): return [None, 0]

    class MockRedis:
        def pipeline(self, transaction=False):
            return MockRedisPipeline()
        async def close(self): pass
        def get(self, *args, **kwargs): return None
        async def xgroup_create(self, *args, **kwargs): pass
        def pubsub(self): return MagicMock()
        async def xreadgroup(self, *args, **kwargs): return []

    with patch("backend.app.main.init_redis_pool", new_callable=AsyncMock) as mock_init:
        mock_init.return_value = MockRedis()
        with patch("backend.app.main.close_redis_pool", new_callable=AsyncMock):
            with patch("redis.StrictRedis", return_value=MockRedis()):
                yield
