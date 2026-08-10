import pytest
import os
from fastapi import FastAPI
from app.main import lifespan
from app.security import auth

@pytest.mark.asyncio
async def test_production_jwt_guard(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(auth, "JWT_SECRET_KEY", "change_me")
    
    app = FastAPI()
    
    with pytest.raises(RuntimeError, match="FATAL: JWT_SECRET_KEY is missing or set to an insecure development"):
        async with lifespan(app):
            pass
