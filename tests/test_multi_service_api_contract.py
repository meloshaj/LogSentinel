from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app.security.auth import get_current_user


@pytest.fixture
def tenant_user_override():
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "tenant-a-user",
        "tenant_id": "tenant-a",
    }
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_logs_api_explicitly_scopes_recent_and_paginated_queries(
    tenant_user_override,
) -> None:
    with (
        patch(
            "backend.app.main.log_repository.get_recent_logs",
            new_callable=AsyncMock,
        ) as recent,
        patch(
            "backend.app.main.log_repository.get_logs_paginated",
            new_callable=AsyncMock,
        ) as paginated,
    ):
        recent.return_value = []
        paginated.return_value = {"items": [], "total": 0, "page": 1, "limit": 200, "pages": 0}
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            recent_response = await client.get("/api/v1/logs/recent?limit=500")
            paginated_response = await client.get("/api/v1/logs?page=1&limit=200")

    assert recent_response.status_code == 200
    assert paginated_response.status_code == 200
    assert recent.await_args.kwargs == {"tenant_id": "tenant-a", "limit": 500}
    assert paginated.await_args.kwargs["tenant_id"] == "tenant-a"
    assert paginated.await_args.kwargs["limit"] == 200
