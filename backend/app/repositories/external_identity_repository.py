"""External identity database persistence layer.

Handles retrieval and creation of external identity records that map
federated provider identities (Microsoft Entra, Google, etc.) to
internal LogSentinel user accounts.

The stable lookup key is always (provider, issuer, subject).
Email is never used as an identity lookup key.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.orm import ExternalIdentityRecord

logger = logging.getLogger("logsentinel.external_identity_repository")


class ExternalIdentityRepository:
    """Repository managing CRUD operations for ExternalIdentityRecord."""

    @staticmethod
    async def get_by_provider_identity(
        db: AsyncSession,
        provider: str,
        issuer: str,
        subject: str,
    ) -> Optional[ExternalIdentityRecord]:
        """Look up an external identity by its stable triple (provider, issuer, subject)."""
        stmt = (
            select(ExternalIdentityRecord)
            .where(ExternalIdentityRecord.provider == provider)
            .where(ExternalIdentityRecord.issuer == issuer)
            .where(ExternalIdentityRecord.subject == subject)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_user_id_and_provider(
        db: AsyncSession,
        user_id: int,
        provider: str,
    ) -> Optional[ExternalIdentityRecord]:
        """Look up an external identity for a specific user and provider."""
        stmt = (
            select(ExternalIdentityRecord)
            .where(ExternalIdentityRecord.user_id == user_id)
            .where(ExternalIdentityRecord.provider == provider)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def create_external_identity(
        db: AsyncSession,
        user_id: int,
        provider: str,
        issuer: str,
        subject: str,
        tenant_id: Optional[str] = None,
        provider_object_id: Optional[str] = None,
        email: Optional[str] = None,
        display_name: Optional[str] = None,
        commit: bool = True,
    ) -> ExternalIdentityRecord:
        """Persist an identity, optionally leaving commit control to the caller."""
        record = ExternalIdentityRecord(
            user_id=user_id,
            provider=provider,
            issuer=issuer,
            subject=subject,
            tenant_id=tenant_id,
            provider_object_id=provider_object_id,
            email=email,
            display_name=display_name,
        )
        db.add(record)
        if commit:
            await db.commit()
            await db.refresh(record)
            logger.info(
                "Created external identity: provider=%s user_id=%d",
                provider,
                user_id,
            )
        else:
            await db.flush()
        return record
