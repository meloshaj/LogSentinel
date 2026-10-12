"""PostgreSQL-authoritative password-reset state machine.

The reset token digest is the durable operation identity.  Token authorization,
password mutation, session invalidation, token completion, and the success
notification outbox row are committed by PostgreSQL as one transaction.
Valkey is deliberately absent from this critical path; its loss therefore
cannot revive a completed reset.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.orm import PasswordResetTokenRecord, UserRecord
from ..core.user_status import ACTIVE
from ..repositories.user_repository import UserRepository
from .email_outbox import enqueue_email
from .password import bounded_hash_password

logger = logging.getLogger("logsentinel.password_reset")

RESET_TOKEN_ISSUED = "issued"
RESET_TOKEN_COMPLETED = "completed"
RESET_TOKEN_EXPIRED = "expired"
RESET_TOKEN_INVALIDATED = "invalidated"


class PasswordResetInvalidError(Exception):
    """The submitted capability is invalid, expired, or no longer usable."""


class PasswordResetUnavailableError(Exception):
    """The durable reset transaction could not be completed or confirmed."""


class PasswordResetTestCrash(Exception):
    """Deterministic disposable-harness interruption; never enabled in production."""


@dataclass(frozen=True)
class PasswordResetCompletion:
    """The durable outcome for one reset capability."""

    already_completed: bool


def digest_reset_token(raw_token: str) -> str:
    """Return the fixed-length durable identity for an opaque reset token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _test_failpoint(name: str) -> None:
    """Raise only for explicitly opted-in non-production crash rehearsals.

    Production intentionally ignores all test-hook environment variables. The
    double guard prevents a copied test setting from becoming a live fault
    injection control.
    """
    if os.getenv("ENVIRONMENT", "development").strip().lower() not in {
        "test",
        "development",
        "ci",
    }:
        return
    if os.getenv("LOGSENTINEL_ALLOW_TEST_HOOKS") != "1":
        return
    if os.getenv("LOGSENTINEL_TEST_FAILPOINT", "").strip() != name:
        return

    marker = os.getenv("LOGSENTINEL_TEST_FAILPOINT_MARKER", "").strip()
    if marker:
        with open(marker, "w", encoding="utf-8") as stream:
            stream.write(name + "\n")
    raise PasswordResetTestCrash(name)


async def _test_delay(name: str) -> None:
    """Delay only an explicitly selected disposable expiry rehearsal point."""
    if os.getenv("ENVIRONMENT", "development").strip().lower() not in {
        "test",
        "development",
        "ci",
    }:
        return
    if os.getenv("LOGSENTINEL_ALLOW_TEST_HOOKS") != "1":
        return
    if os.getenv("LOGSENTINEL_TEST_DELAY_POINT", "").strip() != name:
        return
    try:
        delay_seconds = float(os.getenv("LOGSENTINEL_TEST_DELAY_SECONDS", "0"))
    except ValueError:
        return
    if 0 < delay_seconds <= 10:
        await asyncio.sleep(delay_seconds)


async def _rollback_safely(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception as exc:  # pragma: no cover - defensive cleanup path
        logger.error(
            "Password reset rollback failed: exception_type=%s",
            type(exc).__name__,
        )


async def issue_password_reset(
    db: AsyncSession,
    *,
    user: UserRecord,
    token_digest: str,
    raw_token: str,
    ttl_seconds: int,
) -> None:
    """Persist a reset capability and its delivery work in one DB transaction."""
    try:
        now_result = await db.execute(select(func.clock_timestamp()))
        database_now = now_result.scalar_one()
        if database_now.tzinfo is None:
            database_now = database_now.replace(tzinfo=timezone.utc)
        row = PasswordResetTokenRecord(
            token_digest=token_digest,
            user_id=user.id,
            tenant_id=user.tenant_id,
            issued_at=database_now,
            expires_at=database_now + timedelta(seconds=ttl_seconds),
            state=RESET_TOKEN_ISSUED,
        )
        db.add(row)
        await db.flush()
        await enqueue_email(
            db,
            kind="password_reset",
            recipient=user.email,
            secret=raw_token,
            user_id=user.id,
            tenant_id=user.tenant_id,
            idempotency_key=f"password-reset:{user.id}:{token_digest}",
            commit=False,
        )
        await db.commit()
    except Exception as exc:
        await _rollback_safely(db)
        logger.error(
            "Password reset issuance failed: exception_type=%s",
            type(exc).__name__,
        )
        raise PasswordResetUnavailableError from None


async def complete_password_reset(
    db: AsyncSession,
    *,
    token_digest: str,
    new_password: str,
) -> PasswordResetCompletion:
    """Complete one reset capability or return its committed idempotent result.

    The row lock serializes same-token submissions. A pre-commit interruption
    rolls back all PostgreSQL changes, leaving the token issued and retryable;
    a post-commit interruption observes ``completed`` on the next attempt.
    """
    committed = False
    try:
        _test_failpoint("before_transaction")
        result = await db.execute(
            select(PasswordResetTokenRecord)
            .where(PasswordResetTokenRecord.token_digest == token_digest)
            .with_for_update()
        )
        token = result.scalar_one_or_none()
        if token is None:
            raise PasswordResetInvalidError

        if token.state == RESET_TOKEN_COMPLETED:
            await db.rollback()
            logger.info("reset_already_completed")
            return PasswordResetCompletion(already_completed=True)
        if token.state != RESET_TOKEN_ISSUED:
            raise PasswordResetInvalidError

        now_result = await db.execute(select(func.clock_timestamp()))
        database_now = now_result.scalar_one()
        if database_now.tzinfo is None:
            database_now = database_now.replace(tzinfo=timezone.utc)
        if token.expires_at <= database_now:
            token.state = RESET_TOKEN_EXPIRED
            token.expired_at = database_now
            await db.flush()
            await db.commit()
            committed = True
            logger.info("reset_expired")
            raise PasswordResetInvalidError

        user_result = await db.execute(
            select(UserRecord)
            .where(
                UserRecord.id == token.user_id,
                UserRecord.tenant_id == token.tenant_id,
            )
            .with_for_update()
        )
        user = user_result.scalar_one_or_none()
        if user is None or user.status != ACTIVE:
            token.state = RESET_TOKEN_INVALIDATED
            token.invalidated_at = database_now
            await db.flush()
            await db.commit()
            committed = True
            logger.info("reset_invalidated")
            raise PasswordResetInvalidError

        _test_failpoint("after_token_authorized")
        hashed = await bounded_hash_password(new_password)
        await UserRepository.update_password_with_timestamp(
            db,
            user,
            hashed,
            commit=False,
            after_password_update=lambda: _test_failpoint("after_password_update"),
        )
        _test_failpoint("after_session_revocation")

        await enqueue_email(
            db,
            kind="password_changed",
            recipient=user.email,
            user_id=user.id,
            tenant_id=user.tenant_id,
            idempotency_key=f"password-changed:{token_digest}",
            commit=False,
        )
        _test_failpoint("after_outbox_insert")

        # Recheck against the database wall clock after hashing and all
        # revocation/outbox work. A token that crosses expiry while the
        # bounded hash is running is rolled back rather than committed.
        await _test_delay("before_final_expiry_check")
        final_now_result = await db.execute(select(func.clock_timestamp()))
        final_database_now = final_now_result.scalar_one()
        if final_database_now.tzinfo is None:
            final_database_now = final_database_now.replace(tzinfo=timezone.utc)
        if token.expires_at <= final_database_now:
            raise PasswordResetInvalidError

        token.state = RESET_TOKEN_COMPLETED
        token.completed_at = final_database_now
        await db.flush()
        _test_failpoint("before_commit")
        await db.commit()
        committed = True
        logger.info("reset_committed")
        _test_failpoint("after_commit")
        return PasswordResetCompletion(already_completed=False)
    except PasswordResetTestCrash:
        if not committed:
            await _rollback_safely(db)
        raise
    except PasswordResetInvalidError:
        if not committed:
            await _rollback_safely(db)
        raise
    except Exception as exc:
        if not committed:
            await _rollback_safely(db)
        logger.error(
            "Password reset transaction failed: exception_type=%s",
            type(exc).__name__,
        )
        raise PasswordResetUnavailableError from None
