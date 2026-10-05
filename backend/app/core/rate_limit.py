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


_test_mode = (
    os.getenv("TEST_MODE", "false").strip().lower() == "true"
    or os.getenv("ENVIRONMENT", "").strip().lower() == "test"
)
_rate_limit_storage = (
    "memory://"
    if _test_mode
    else os.getenv("RATE_LIMIT_STORAGE_URI") or os.getenv("REDIS_URL") or "memory://"
)

limiter = Limiter(key_func=tenant_or_client_key, storage_uri=_rate_limit_storage)
