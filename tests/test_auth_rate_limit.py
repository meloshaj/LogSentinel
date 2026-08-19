from fastapi.testclient import TestClient

from backend.app.main import app


from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.core.database import get_async_session


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    mock_execute_result = MagicMock()
    
    # Mock user object
    user = MagicMock()
    user.hashed_password = "hashed"
    
    mock_execute_result.scalar_one_or_none.return_value = user
    db.execute.return_value = mock_execute_result
    return db

@pytest.fixture
def client(mock_db: AsyncMock) -> TestClient:
    app.dependency_overrides[get_async_session] = lambda: mock_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_auth_login_rate_limit(client: TestClient):
    # Reset limiter for this client just in case
    app.state.limiter.reset()
    
    # The limit is 5 per minute for /api/auth/login
    for _ in range(5):
        response = client.post("/api/auth/login", json={"email": "test@example.com", "password": "wrong"})
        assert response.status_code != 429
    
    # 6th request should hit 429
    response = client.post("/api/auth/login", json={"email": "test@example.com", "password": "wrong"})
    assert response.status_code == 429
