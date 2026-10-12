"""Server-controlled browser session and rotating refresh-token lifecycle."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.orm import AuthSessionRecord, RefreshTokenRecord, UserRecord
from ..core.user_status import ACTIVE
from ..security.tenants import resolve_membership

REFRESH_COOKIE_NAME = "logsentinel_refresh"
CSRF_COOKIE_NAME = "logsentinel_csrf"
REFRESH_TTL_DAYS = 14


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IssuedSession:
    session_id: str
    refresh_token: str
    csrf_token: str
    expires_at: datetime


async def create_session(db: AsyncSession, user: UserRecord) -> IssuedSession:
    now = datetime.now(timezone.utc)
    session_id = secrets.token_urlsafe(32)
    refresh = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    expires = now + timedelta(days=REFRESH_TTL_DAYS)
    db.add(
        AuthSessionRecord(
            id=session_id,
            user_id=user.id,
            tenant_id=user.tenant_id,
            csrf_hash=_hash(csrf),
            created_at=now,
            expires_at=expires,
            last_used_at=now,
        )
    )
    db.add(
        RefreshTokenRecord(
            token_hash=_hash(refresh),
            session_id=session_id,
            created_at=now,
            expires_at=expires,
        )
    )
    await db.commit()
    return IssuedSession(session_id, refresh, csrf, expires)


async def rotate_refresh_token(
    db: AsyncSession, raw_token: str, csrf_token: str
) -> tuple[UserRecord, IssuedSession] | None:
    now = datetime.now(timezone.utc)
    stmt = (
        select(RefreshTokenRecord, AuthSessionRecord, UserRecord)
        .join(AuthSessionRecord, AuthSessionRecord.id == RefreshTokenRecord.session_id)
        .join(UserRecord, UserRecord.id == AuthSessionRecord.user_id)
        .where(RefreshTokenRecord.token_hash == _hash(raw_token))
        .with_for_update()
    )
    result = await db.execute(stmt)
    row = result.first()
    if row is None:
        return None
    token, session, user = row
    if token.consumed_at is not None:
        session.revoked_at = now
        session.reuse_detected_at = now
        await db.commit()
        return None
    if (
        token.revoked_at is not None
        or token.expires_at <= now
        or session.revoked_at is not None
        or session.expires_at <= now
        or user.status != ACTIVE
        or _hash(csrf_token) != session.csrf_hash
    ):
        session.revoked_at = session.revoked_at or now
        await db.commit()
        return None

    # Refresh is also an authenticated tenant operation. Re-resolve the
    # membership so a previously issued session cannot survive suspension or
    # tenant shutdown. Lightweight legacy unit doubles without tenant state
    # do not exercise the database-backed tenant contract.
    if getattr(user, "tenant_id", None):
        try:
            await resolve_membership(db, user)
        except Exception:
            session.revoked_at = session.revoked_at or now
            await db.commit()
            return None

    next_refresh = secrets.token_urlsafe(48)
    next_hash = _hash(next_refresh)
    token.consumed_at = now
    token.last_used_at = now
    token.rotated_to = next_hash
    session.last_used_at = now
    db.add(
        RefreshTokenRecord(
            token_hash=next_hash,
            session_id=session.id,
            created_at=now,
            expires_at=session.expires_at,
            rotated_from=token.token_hash,
        )
    )
    await db.commit()
    return user, IssuedSession(session.id, next_refresh, csrf_token, session.expires_at)


async def revoke_session(db: AsyncSession, raw_token: str | None) -> None:
    if not raw_token:
        return
    result = await db.execute(
        select(RefreshTokenRecord).where(
            RefreshTokenRecord.token_hash == _hash(raw_token)
        )
    )
    token = result.scalar_one_or_none()
    if token is None:
        return
    now = datetime.now(timezone.utc)
    await db.execute(
        update(AuthSessionRecord)
        .where(AuthSessionRecord.id == token.session_id)
        .values(revoked_at=now)
    )
    await db.commit()


async def revoke_all_user_sessions(db: AsyncSession, user_id: int) -> None:
    await db.execute(
        update(AuthSessionRecord)
        .where(
            AuthSessionRecord.user_id == user_id, AuthSessionRecord.revoked_at.is_(None)
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )
    await db.commit()
