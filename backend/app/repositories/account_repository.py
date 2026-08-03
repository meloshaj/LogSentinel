"""Account database persistence layer.

Handles retrieval and creation of account records in PostgreSQL using the
AsyncSession ORM adapter.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.orm import AccountRecord

logger = logging.getLogger("logsentinel.account_repository")


class AccountRepository:
    """Repository class managing CRUD operations for AccountRecord ORM models."""

    @staticmethod
    async def get_account_by_provider(
        db: AsyncSession,
        provider: str,
        provider_account_id: str,
    ) -> Optional[AccountRecord]:
        """Look up an account by its unique provider and providerAccountId pair."""
        stmt = (
            select(AccountRecord)
            .where(AccountRecord.provider == provider)
            .where(AccountRecord.provider_account_id == provider_account_id)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_accounts_by_user_id(
        db: AsyncSession,
        user_id: int,
    ) -> list[AccountRecord]:
        """Look up all accounts linked to a specific user."""
        stmt = select(AccountRecord).where(AccountRecord.user_id == user_id)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def create_account(
        db: AsyncSession,
        user_id: int,
        provider: str,
        provider_account_id: str,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        commit: bool = True,
    ) -> AccountRecord:
        """Persist an account, optionally leaving commit control to the caller."""
        record = AccountRecord(
            user_id=user_id,
            provider=provider,
            provider_account_id=provider_account_id,
            access_token=access_token,
            refresh_token=refresh_token,
        )
        db.add(record)
        if commit:
            await db.commit()
            await db.refresh(record)
            logger.info(
                "Created account mapping: provider=%s user_id=%d",
                provider,
                user_id,
            )
        else:
            await db.flush()
        return record
