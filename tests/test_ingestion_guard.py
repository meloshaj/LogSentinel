import asyncio
from unittest.mock import patch

from fastapi import HTTPException
import pytest

from backend.app.core.settings import get_ingestion_security_settings
from backend.app.security.ingest_guard import require_ingestion_api_key


def test_ingestion_security_settings_combines_and_strips_keys() -> None:
    env = {
        "INGEST_API_KEY": " primary-key ",
        "INGEST_API_KEYS": " rotated-key, ,secondary-key ",
    }

    with patch.dict("os.environ", env, clear=False):
        settings = get_ingestion_security_settings()

    assert settings.api_keys == ("primary-key", "rotated-key", "secondary-key")
    assert settings.configured is True


def test_guard_allows_valid_key() -> None:
    with patch.dict("os.environ", {"INGEST_API_KEY": "valid-key"}, clear=False):
        result = asyncio.run(require_ingestion_api_key("valid-key"))

    assert result is None


def test_guard_rejects_missing_key() -> None:
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(require_ingestion_api_key(None))

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "missing_api_key"


def test_guard_rejects_invalid_key_without_leaking_configured_key() -> None:
    with patch.dict("os.environ", {"INGEST_API_KEY": "valid-key"}, clear=False):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(require_ingestion_api_key("invalid-key"))

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "invalid_api_key"
    assert "valid-key" not in str(exc_info.value.detail)


def test_guard_fails_closed_without_configured_keys() -> None:
    with patch.dict("os.environ", {"INGEST_API_KEY": "", "INGEST_API_KEYS": ""}, clear=False):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(require_ingestion_api_key("any-key"))

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "ingestion_guard_not_configured"
