"""Authentication and authorization helper utilities.

Provides password hashing using Argon2id (with bcrypt legacy support),
JWT generation with ``iat`` claims, JWT verification with
``password_changed_at`` session invalidation, and the FastAPI
dependency to authenticate and resolve the current user.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.database import get_async_session
from ..core.email_identity import canonicalize_email
from ..core.orm import UserRecord
from ..core.user_status import ACTIVE
from ..services.password import (
    verify_and_update_password,
)

# JWT Configuration
JWT_SECRET_KEY = (
    os.getenv("JWT_SECRET_KEY")
    or "j6nXLp4jdPIYuoGC20uNKMgG2KhYVeEyaHqxECoYXygCQ3nrgQvULL9YlIn6eGye"
)
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

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


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
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
        }
    )
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security_scheme)],
    db: Annotated[AsyncSession, Depends(get_async_session)],
) -> UserRecord:
    """FastAPI dependency to extract and authenticate JWT and return the user record.

    Security checks:
        1. Decode and validate the JWT signature and expiration.
        2. Resolve the user by the ``sub`` (email) claim.
        3. If the user has a ``password_changed_at`` timestamp, reject
           any token whose ``iat`` predates that timestamp.  This
           provides immediate global session revocation after a
           password change without requiring a token blocklist.

    Raises HTTP 401 if token is expired, invalid, revoked, or user
    doesn't exist.
    """
    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
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
