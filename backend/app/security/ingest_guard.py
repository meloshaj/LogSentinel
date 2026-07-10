"""Stateless API-key guard for machine-to-machine log ingestion."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Header, HTTPException, status

from ..core.settings import get_ingestion_security_settings


async def require_ingestion_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Require a configured ingestion API key before accepting log payloads."""
    if x_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing_api_key",
        )

    settings = get_ingestion_security_settings()
    if not settings.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ingestion_guard_not_configured",
        )

    if not any(secrets.compare_digest(x_api_key, valid_key) for valid_key in settings.api_keys):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid_api_key",
        )
