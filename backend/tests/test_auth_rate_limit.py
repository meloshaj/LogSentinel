from fastapi.testclient import TestClient
from app.main import app

def test_auth_login_rate_limit():
    with TestClient(app) as client:
        # Reset limiter for this client just in case
        app.state.limiter.reset()
        
        # The limit is 5 per minute for /api/auth/login
        for _ in range(5):
            response = client.post("/api/auth/login", json={"email": "test@example.com", "password": "wrong"})
            assert response.status_code != 429
        
        # 6th request should hit 429
        response = client.post("/api/auth/login", json={"email": "test@example.com", "password": "wrong"})
        assert response.status_code == 429
