"""User database persistence layer.

Handles retrieval and creation of user records in PostgreSQL using the
AsyncSession ORM adapter.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.orm import UserRecord

logger = logging.getLogger("logsentinel.user_repository")


class UserRepository:
    """Repository class managing CRUD operations for UserRecord ORM models."""

    @staticmethod
    async def get_user_by_email(db: AsyncSession, email: str) -> Optional[UserRecord]:
        """Retrieve a user by their unique email address."""
        stmt = select(UserRecord).where(UserRecord.email == email)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[UserRecord]:
        """Retrieve a user by their primary key id."""
        stmt = select(UserRecord).where(UserRecord.id == user_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def create_user(
        db: AsyncSession,
        email: str,
        hashed_password: Optional[str] = None,
        full_name: Optional[str] = None,
        organization: Optional[str] = None,
    ) -> UserRecord:
        """Persist a new user record to the database and commit the transaction."""
        new_user = UserRecord(
            email=email.strip().lower(),
            hashed_password=hashed_password,
            full_name=full_name,
            organization=organization,
        )
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)
        logger.info("Successfully registered user: %s", email)
        return new_user

    @staticmethod
    async def update_password(
        db: AsyncSession,
        user: UserRecord,
        hashed_password: str,
    ) -> UserRecord:
        """Update a user's hashed password and commit."""
        user.hashed_password = hashed_password
        await db.commit()
        await db.refresh(user)
        logger.info("Password updated for user: %s", user.email)
        return user

