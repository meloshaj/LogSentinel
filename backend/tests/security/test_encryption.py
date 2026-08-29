import importlib

import pytest
from backend.app.core import orm


def test_encryption_key_crash_on_missing(monkeypatch):
    """Verify that initializing orm.py without ENCRYPTION_KEY raises ValueError."""
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)

    with pytest.raises(
        ValueError, match="ENCRYPTION_KEY environment variable is not set"
    ):
        importlib.reload(orm)


def test_encryption_key_crash_on_invalid(monkeypatch):
    """Verify that initializing orm.py with invalid ENCRYPTION_KEY raises ValueError."""
    monkeypatch.setenv("ENCRYPTION_KEY", "invalid_base64_and_length")

    with pytest.raises(ValueError):
        importlib.reload(orm)

    # Restore valid state for other tests
    import secrets
    import base64

    test_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("utf-8")
    monkeypatch.setenv("ENCRYPTION_KEY", test_key)
    importlib.reload(orm)
