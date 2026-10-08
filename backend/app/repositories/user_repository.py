"""User database persistence layer.

Handles retrieval and creation of user records in PostgreSQL using the
AsyncSession ORM adapter.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.email_identity import canonicalize_email
from ..core.orm import (
    AuthSessionRecord,
    RefreshTokenRecord,
    TenantMembershipRecord,
    UserRecord,
)
from ..core.user_status import ACTIVE, PENDING_VERIFICATION
from ..security.tenants import provision_external_identity, provision_tenant

logger = logging.getLogger("logsentinel.user_repository")


class UserRepository:
    """Repository class managing CRUD operations for UserRecord ORM models."""

    @staticmethod
    async def get_user_by_email(db: AsyncSession, email: str) -> UserRecord | None:
        """Retrieve a user by their unique email address."""
        stmt = select(UserRecord).where(UserRecord.email == canonicalize_email(email))
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_user_by_email_for_update(
        db: AsyncSession, email: str
    ) -> UserRecord | None:
        """Retrieve a user by their unique email address with a row-level lock."""
        stmt = (
            select(UserRecord)
            .where(UserRecord.email == canonicalize_email(email))
            .with_for_update()
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_user_by_id(db: AsyncSession, user_id: int) -> UserRecord | None:
        """Retrieve a user by their primary key id."""
        stmt = select(UserRecord).where(UserRecord.id == user_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def create_user(
        db: AsyncSession,
        email: str,
        hashed_password: str | None = None,
        full_name: str | None = None,
        organization: str | None = None,
        tenant_id: str | None = None,
        status: str = ACTIVE,
        commit: bool = True,
        provider: str | None = None,
        issuer: str | None = None,
        provider_tenant_id: str | None = None,
        provider_subject: str | None = None,
        verified_email: str | None = None,
    ) -> UserRecord:
        """Persist a user, optionally leaving commit control to the caller."""
        now = datetime.now(timezone.utc)
        if tenant_id is not None:
            raise ValueError(
                "Tenant enrollment requires a verified provider mapping or invitation"
            )
        if provider is None:
            tenant_id, role = await provision_tenant(db)
        else:
            tenant_id, role = await provision_external_identity(
                db,
                provider=provider,
                issuer=issuer or "",
                provider_subject=provider_subject or "",
                provider_tenant_id=provider_tenant_id,
                verified_email=verified_email or email,
            )
        new_user = UserRecord(
            email=canonicalize_email(email),
            hashed_password=hashed_password,
            full_name=full_name,
            organization=organization,
            tenant_id=tenant_id,
            status=status,
            role=role,
            email_verified_at=now if status == ACTIVE else None,
        )
        db.add(new_user)
        await db.flush()
        db.add(
            TenantMembershipRecord(
                tenant_id=tenant_id, user_id=new_user.id, role=role, status="active"
            )
        )
        if commit:
            await db.commit()
            await db.refresh(new_user)
            logger.info(
                "Successfully registered user id=%s status=%s", new_user.id, status
            )
        else:
            await db.flush()
        return new_user

    @staticmethod
    async def update_password(
        db: AsyncSession,
        user: UserRecord,
        hashed_password: str,
    ) -> UserRecord:
        """Update a user's hashed password and commit."""
        user.hashed_password = hashed_password
        user.password_changed_at = datetime.now(timezone.utc)
        from sqlalchemy import update

        await db.execute(
            update(AuthSessionRecord)
            .where(AuthSessionRecord.user_id == user.id)
            .values(revoked_at=datetime.now(timezone.utc))
        )
        await db.commit()
        await db.refresh(user)
        logger.info("Password updated for user_id=%s", user.id)
        return user

    @staticmethod
    async def activate_user(db: AsyncSession, user: UserRecord) -> UserRecord:
        """Transition a user from pending_verification to active.

        Sets ``status = 'active'`` and records ``email_verified_at``.
        """
        if user.status != PENDING_VERIFICATION:
            raise ValueError("Only pending-verification users can be activated")

        user.status = ACTIVE
        user.email_verified_at = datetime.now(timezone.utc).replace(microsecond=0)
        await db.commit()
        await db.refresh(user)
        logger.info("User activated: id=%s", user.id)
        return user

    @staticmethod
    async def update_password_with_timestamp(
        db: AsyncSession,
        user: UserRecord,
        hashed_password: str,
        commit: bool = True,
        after_password_update: Callable[[], None] | None = None,
    ) -> UserRecord:
        """Update password and set password_changed_at for JWT invalidation.

        All JWTs with ``iat`` before ``password_changed_at`` will be
        rejected by ``get_current_user``.
        """
        user.hashed_password = hashed_password
        user.password_changed_at = func.date_trunc("second", func.now())
        if after_password_update is not None:
            after_password_update()
        from sqlalchemy import update

        await db.execute(
            update(AuthSessionRecord)
            .where(AuthSessionRecord.user_id == user.id)
            .values(revoked_at=datetime.now(timezone.utc))
        )
        await db.execute(
            update(RefreshTokenRecord)
            .where(
                RefreshTokenRecord.session_id.in_(
                    select(AuthSessionRecord.id).where(
                        AuthSessionRecord.user_id == user.id
                    )
                ),
                RefreshTokenRecord.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(timezone.utc))
        )
        if commit:
            await db.commit()
            await db.refresh(user)
        else:
            await db.flush()
        logger.info("Password update staged with timestamp for user: id=%s", user.id)
        return user

    @staticmethod
    async def update_hashed_password_silent(
        db: AsyncSession,
        user: UserRecord,
        hashed_password: str,
    ) -> None:
        """Update the password hash without changing password_changed_at.

        Used for transparent algorithm upgrades (bcrypt → Argon2id)
        during normal login — not a user-initiated password change.
        """
        user.hashed_password = hashed_password
        await db.commit()
        await db.refresh(user)
        logger.info(
            "Password hash upgraded (algorithm migration) for user: id=%s", user.id
        )

    @staticmethod
    async def cleanup_pending_users(
        db: AsyncSession,
        max_age_hours: int = 24,
    ) -> int:
        """Delete unverified users older than ``max_age_hours``.

        Returns the number of deleted rows.
        """
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        total_deleted = 0

        while True:
            # Fetch a bounded batch of IDs to prevent table locks and large WAL generation
            subq = (
                select(UserRecord.id)
                .where(UserRecord.status == PENDING_VERIFICATION)
                .where(UserRecord.created_at < cutoff)
                .limit(1000)
            ).scalar_subquery()

            delete_stmt = delete(UserRecord).where(UserRecord.id.in_(subq))
            result = await db.execute(delete_stmt)
            await db.commit()

            deleted_in_batch = result.rowcount  # type: ignore[attr-defined]
            if deleted_in_batch == 0:
                break

            total_deleted += deleted_in_batch

        if total_deleted > 0:
            logger.info(
                "Cleaned up %d pending-verification users older than %dh (in batches)",
                total_deleted,
                max_age_hours,
            )
        return total_deleted
