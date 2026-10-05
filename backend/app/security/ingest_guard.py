"""Stateless API-key guard for machine-to-machine log ingestion."""

from __future__ import annotations

import secrets
import hashlib
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.database import get_async_session
from ..core.settings import get_ingestion_security_settings
from ..core.orm import (
    IngestionApiKeyRecord,
    TenantMembershipRecord,
    TenantRecord,
    UserRecord,
)
from .data_scope import DataScope


REQUIRED_INGESTION_SCOPE = "logs:ingest"


def has_required_ingestion_scope(scopes: object) -> bool:
    """Return whether persisted scope metadata authorizes log ingestion.

    Missing, malformed, and legacy scope values are intentionally denied;
    there is no implicit wildcard or compatibility grant.
    """
    return isinstance(scopes, list) and REQUIRED_INGESTION_SCOPE in {
        str(scope) for scope in scopes
    }


async def _optional_async_session():
    """Allow isolated route tests to use env keys before a DB lifespan exists."""
    try:
        async for session in get_async_session():
            yield session
    except RuntimeError:
        yield None


async def require_ingestion_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    db: AsyncSession | None = Depends(_optional_async_session),
) -> DataScope:
    """Require a configured ingestion API key before accepting log payloads.
    Returns the immutable tenant/owner relationship associated with the key."""
    if x_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing_api_key",
        )

    if db is not None and isinstance(db, AsyncSession):
        digest = hashlib.sha256(x_api_key.encode("utf-8")).hexdigest()
        result = await db.execute(
            select(IngestionApiKeyRecord)
            .join(UserRecord, UserRecord.id == IngestionApiKeyRecord.user_id)
            .join(TenantRecord, TenantRecord.id == IngestionApiKeyRecord.tenant_id)
            .join(
                TenantMembershipRecord,
                (TenantMembershipRecord.tenant_id == IngestionApiKeyRecord.tenant_id)
                & (TenantMembershipRecord.user_id == IngestionApiKeyRecord.user_id),
            )
            .where(
                IngestionApiKeyRecord.key_hash == digest,
                IngestionApiKeyRecord.revoked_at.is_(None),
                IngestionApiKeyRecord.expires_at.is_not(None),
                IngestionApiKeyRecord.expires_at > datetime.now(timezone.utc),
                UserRecord.tenant_id == IngestionApiKeyRecord.tenant_id,
                UserRecord.status == "active",
                TenantRecord.status == "active",
                TenantMembershipRecord.status == "active",
            )
        )
        record = result.scalar_one_or_none()
        if record is not None:
            scopes = record.scopes
            if (
                record.expires_at is not None
                and record.expires_at > datetime.now(timezone.utc)
                and has_required_ingestion_scope(scopes)
            ):
                record.last_used_at = datetime.now(timezone.utc)
                await db.commit()
                return DataScope(record.tenant_id, record.user_id)
            # A database key was found but is malformed, expired, unscoped,
            # revoked, or otherwise not currently authorized. Never fall back
            # to a static key with the same plaintext.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="invalid_api_key"
            )

        # Static environment keys have no authoritative application-user
        # owner. They can never ingest user-owned operational data.
        settings = get_ingestion_security_settings()
        if not settings.configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="ingestion_guard_not_configured",
            )

        for valid_key in settings.api_keys:
            if secrets.compare_digest(x_api_key, valid_key):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="legacy_ingestion_key_has_no_owner",
                )

    else:
        settings = get_ingestion_security_settings()
        if not settings.configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="ingestion_guard_not_configured",
            )

        for valid_key in settings.api_keys:
            if secrets.compare_digest(x_api_key, valid_key):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="legacy_ingestion_key_has_no_owner",
                )

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="invalid_api_key",
    )
