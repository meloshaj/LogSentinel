from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app.security.auth import get_current_user

@pytest.fixture(autouse=True)
def override_auth():
    app.dependency_overrides[get_current_user] = lambda: {"sub": "test"}
    yield
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_get_recent_logs():
    with patch("backend.app.main.log_repository.get_recent_logs", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = [{"id": "1", "message": "test"}]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/api/v1/logs/recent")
        assert response.status_code == 200
        assert response.json() == {"logs": [{"id": "1", "message": "test"}]}

@pytest.mark.asyncio
async def test_get_logs_paginated():
    with patch("backend.app.main.log_repository.get_logs_paginated", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"logs": [], "total": 0}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/api/v1/logs?page=1&limit=50")
        assert response.status_code == 200
        assert response.json() == {"logs": [], "total": 0}
