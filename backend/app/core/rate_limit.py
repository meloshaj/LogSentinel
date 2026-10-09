"""Shared rate-limiter instance for the LogSentinel API.

Separated into its own module to avoid circular imports between
``main.py`` (which registers the limiter on the app) and router
modules that apply ``@limiter.limit()`` decorators.
"""

import hashlib
import os

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def tenant_or_client_key(request: Request) -> str:
    """Use the credential identity for ingestion limits without storing secrets."""
    api_key = request.headers.get("X-API-Key", "").strip()
    if api_key:
        return "tenant-key:" + hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    return "client:" + get_remote_address(request)


def is_test_mode() -> bool:
    """Check whether the application is running under test execution."""
    return (
        os.getenv("TEST_MODE", "false").strip().lower() == "true"
        or os.getenv("ENVIRONMENT", "").strip().lower() == "test"
    )


def get_ingest_rate_limit() -> str:
    """Return the ingestion rate limit, configurable via INGEST_RATE_LIMIT.

    Defaults to 1,000,000/minute in test mode and 100,000/minute in production to accommodate
    high-throughput streaming pipelines and burst testing.
    """
    default_limit = "1000000/minute" if is_test_mode() else "100000/minute"
    return os.getenv("INGEST_RATE_LIMIT", default_limit)


_rate_limit_storage = (
    "memory://"
    if is_test_mode()
    else os.getenv("RATE_LIMIT_STORAGE_URI") or os.getenv("REDIS_URL") or "memory://"
)

limiter = Limiter(key_func=tenant_or_client_key, storage_uri=_rate_limit_storage)
