
import pytest
from fastapi import FastAPI

from backend.app.main import lifespan
from backend.app.security import auth


@pytest.mark.asyncio
async def test_production_jwt_guard(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("RUN_EMBEDDED_WORKERS", "false")
    monkeypatch.setenv("RUN_WEBHOOK_WORKER_IN_LIFESPAN", "false")
    monkeypatch.setenv("RUN_ARCHIVE_WORKER_IN_LIFESPAN", "false")
    monkeypatch.setattr("backend.app.main.run_embedded_workers", False)
    monkeypatch.setattr("backend.app.main.run_webhook_worker_in_lifespan", False)
    monkeypatch.setattr("backend.app.main.run_archive_worker_in_lifespan", False)
    monkeypatch.setattr(auth, "JWT_SECRET_KEY", "change_me")
    
    app = FastAPI()
    
    with pytest.raises(RuntimeError, match="FATAL: JWT_SECRET_KEY is missing or set to an insecure value"):
        async with lifespan(app):
            pass
