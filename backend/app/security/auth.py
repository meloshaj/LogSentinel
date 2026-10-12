"""Authentication and authorization helper utilities.

Provides password hashing using Argon2id (with bcrypt legacy support),
JWT generation with ``iat`` claims, JWT verification with
``password_changed_at`` session invalidation, and the FastAPI
dependency to authenticate and resolve the current user.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.database import get_async_session
from ..core.email_identity import canonicalize_email
from ..core.orm import AuthSessionRecord, UserRecord
from ..core.user_status import ACTIVE
from ..services.password import hash_password as _hash_password
from ..services.password import verify_and_update_password

# Preserve the historical import path used by integrations and older tests;
# new authentication flows use the bounded service wrappers directly.
hash_password = _hash_password

# JWT Configuration
_jwt_secret_key = os.getenv("JWT_SECRET_KEY")
if not _jwt_secret_key or len(_jwt_secret_key) < 32:
    raise RuntimeError(
        "JWT_SECRET_KEY must be configured with at least 32 characters before importing authentication code"
    )
JWT_SECRET_KEY: str = _jwt_secret_key
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10"))
JWT_ISSUER = os.getenv("JWT_ISSUER", "logsentinel")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "logsentinel-api")
if not JWT_ISSUER.strip() or not JWT_AUDIENCE.strip():
    raise RuntimeError("JWT_ISSUER and JWT_AUDIENCE must be configured")

# HTTP Bearer Scheme
security_scheme = HTTPBearer()


# ---------------------------------------------------------------------------
# Legacy thin wrappers — retain the original function signatures so that
# existing callers (tests, SSO routes) continue to work unchanged.
# ---------------------------------------------------------------------------


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain-text password against a stored hash (bcrypt or Argon2id).

    This is a compatibility wrapper.  For new code, prefer
    ``verify_and_update_password`` which also returns an upgraded hash.
    """
    valid, _ = verify_and_update_password(plain_password, hashed_password)
    return valid


def create_access_token(
    data: dict, expires_delta: timedelta | None = None, session_id: str | None = None
) -> str:
    """Create a new JSON Web Token (JWT) with ``exp`` and ``iat`` claims.

    The ``iat`` (issued-at) claim is used by ``get_current_user`` to
    reject tokens issued before the user's most recent password change.
    """
    to_encode = data.copy()
    now = datetime.now(timezone.utc)

    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update(
        {
            "exp": expire,
            "iat": now,
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "jti": secrets.token_urlsafe(24),
            **({"sid": session_id} if session_id else {}),
        }
    )
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


async def authenticate_token(token: str, db: AsyncSession) -> UserRecord:
    """Validate a bearer token against the authoritative database user state.

    Security checks:
        1. Decode and validate the JWT signature and expiration.
        2. Resolve the user by the ``sub`` (email) claim.
        3. If the user has a ``password_changed_at`` timestamp, reject
           any token whose ``iat`` predates that timestamp.  This
           provides immediate global session revocation after a
           password change without requiring a token blocklist.

    Raises HTTP 401 if token is expired, invalid, revoked, or user doesn't exist.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            options={"require": ["sub", "exp", "iat", "iss", "aud", "jti"]},
        )
        email: str | None = payload.get("sub")
        if email is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception

    # Query database to find the user
    stmt = select(UserRecord).where(UserRecord.email == canonicalize_email(email))
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception

    # A suspended or otherwise non-operational account must not retain access
    # through a token issued before the state transition.
    # ``None`` is accepted only for compatibility with pre-migration ORM
    # objects in older callers; the production schema is NOT NULL and the
    # migration backfills every row to ``active``.
    if user.status is not None and user.status != ACTIVE:
        raise credentials_exception

    session_id = payload.get("sid")
    if session_id:
        session_result = await db.execute(
            select(AuthSessionRecord).where(AuthSessionRecord.id == session_id)
        )
        auth_session = session_result.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if (
            auth_session is None
            or auth_session.user_id != user.id
            or auth_session.revoked_at is not None
            or auth_session.expires_at <= now
        ):
            raise credentials_exception

    # Session invalidation: reject tokens issued before password change
    if user.password_changed_at is None:
        return user

    issued_at = payload.get("iat")
    if issued_at is None:
        raise credentials_exception
    if issued_at is not None:
        token_issued = datetime.fromtimestamp(issued_at, tz=timezone.utc)
        # Normalize database timestamp to remove microseconds before comparing against JWT Unix seconds
        safe_changed_at = user.password_changed_at.replace(microsecond=0)
        if safe_changed_at.tzinfo is None:
            safe_changed_at = safe_changed_at.replace(tzinfo=timezone.utc)
        else:
            safe_changed_at = safe_changed_at.astimezone(timezone.utc)

        # Allow up to 5 seconds of NTP clock skew drift
        if (token_issued.timestamp() + 5) < safe_changed_at.timestamp():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token invalidated due to password change",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return user


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security_scheme)],
    db: Annotated[AsyncSession, Depends(get_async_session)],
) -> UserRecord:
    """FastAPI dependency to extract and authenticate JWT and return the user record."""
    return await authenticate_token(credentials.credentials, db)
