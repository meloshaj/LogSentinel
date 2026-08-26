import os
import pytest
from prometheus_client import REGISTRY

# Set test environment variables BEFORE any app modules are imported
os.environ["ENVIRONMENT"] = "test"
os.environ["ENCRYPTION_KEY"] = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="
os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-minimum-length-for-hs256"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@localhost:5432/logsentinel_test"

@pytest.fixture(autouse=True)
def clean_prometheus_registry():
    """Clear dynamically registered collectors between test runs to prevent duplicate metric panics."""
    original_collectors = set(REGISTRY._collector_to_names.keys())
    yield
    collectors_to_unregister = set()
    for collector in list(REGISTRY._collector_to_names.keys()):
        if collector not in original_collectors:
            collectors_to_unregister.add(collector)
    for collector in collectors_to_unregister:
        REGISTRY.unregister(collector)
