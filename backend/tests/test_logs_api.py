import pytest
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.mark.asyncio
async def test_get_recent_logs():
    with patch("app.main.log_repository.get_recent_logs", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = [{"id": "1", "message": "test"}]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/api/v1/logs/recent")
        assert response.status_code == 200
        assert response.json() == {"logs": [{"id": "1", "message": "test"}]}

@pytest.mark.asyncio
async def test_get_logs_paginated():
    with patch("app.main.log_repository.get_logs_paginated", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"logs": [], "total": 0}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/api/v1/logs?page=1&limit=50")
        assert response.status_code == 200
        assert response.json() == {"logs": [], "total": 0}
