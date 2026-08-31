"""Unified password hashing and cryptographic token service.

Provides Argon2id as the primary password hash algorithm with transparent
bcrypt legacy migration.  Also contains verification code generation,
HMAC hashing, and opaque reset-token helpers.

Security invariants:
    * New passwords are always hashed with Argon2id.
    * Legacy bcrypt hashes are transparently upgraded on successful login.
    * A module-level timing sentinel absorbs computation time for
      non-existent users, preventing username-enumeration via latency.
    * Verification codes are 6-digit, cryptographically random.
    * Reset tokens carry 256 bits of entropy (``secrets.token_urlsafe(32)``).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import secrets

import bcrypt
from argon2 import PasswordHasher
from argon2.exceptions import HashingError, VerificationError, VerifyMismatchError
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

from ..core.settings import get_auth_security_settings

logger = logging.getLogger("logsentinel.password")

# ---------------------------------------------------------------------------
# Argon2id configuration — OWASP 2024 recommended parameters
# ---------------------------------------------------------------------------
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


# ---------------------------------------------------------------------------
# HMAC key for verification code hashing
# ---------------------------------------------------------------------------
_HMAC_KEY: bytes = os.getenv("JWT_SECRET_KEY", "").encode("utf-8")


# ---------------------------------------------------------------------------
# Public API — Password hashing
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Hash a plaintext password using Argon2id.

    Returns the PHC-format hash string (starts with ``$argon2id$``).
    """
    return _hasher.hash(password)


def verify_and_update_password(
    plain_password: str,
    stored_hash: str,
) -> tuple[bool, str | None]:
    """Verify a password and optionally return an upgraded hash.

    Returns:
        ``(True, new_hash)``  — password matches; ``new_hash`` is non-None
                                when the stored hash should be updated
                                (bcrypt → Argon2id migration, or Argon2id
                                parameter refresh).
        ``(False, None)``     — password does not match.
    """
    if stored_hash.startswith(("$2b$", "$2a$")):
        # Legacy bcrypt path
        try:
            valid = bcrypt.checkpw(
                plain_password.encode("utf-8"),
                stored_hash.encode("utf-8"),
            )
        except Exception:
            return False, None
        if valid:
            # Transparently upgrade to Argon2id
            new_hash = hash_password(plain_password)
            logger.info("Upgrading bcrypt hash to Argon2id on successful login")
            return True, new_hash
        return False, None

    # Argon2id path
    try:
        _hasher.verify(stored_hash, plain_password)
    except VerifyMismatchError:
        return False, None
    except (VerificationError, HashingError):
        return False, None

    # Check if parameters have changed and a rehash is needed
    if _hasher.check_needs_rehash(stored_hash):
        new_hash = hash_password(plain_password)
        logger.info("Rehashing Argon2id with updated parameters")
        return True, new_hash

    return True, None


# ---------------------------------------------------------------------------
# Timing sentinel — absorbs computation time for non-existent user lookups
# to prevent username enumeration via response-timing deltas.
# ---------------------------------------------------------------------------
_TIMING_SENTINEL: str = hash_password("timing_sentinel_guard")


def verify_timing_sentinel(password: str) -> None:
    """Perform a dummy password verification to normalise response latency.

    Called when the target user does not exist so that the endpoint
    still pays the full Argon2id cost before returning 401.
    """
    verify_and_update_password(password, _TIMING_SENTINEL)


# ---------------------------------------------------------------------------
# Bounded Asynchronous Hashing (Resource Starvation Defense)
# ---------------------------------------------------------------------------

_SECURITY_SETTINGS = get_auth_security_settings()
_HASH_SEMAPHORE = asyncio.Semaphore(_SECURITY_SETTINGS.max_concurrent_hashes_per_worker)
logger.info(
    "Initialized Argon2id semaphore limit: %d (Max projected footprint: %d MiB)",
    _SECURITY_SETTINGS.max_concurrent_hashes_per_worker,
    _SECURITY_SETTINGS.max_concurrent_hashes_per_worker * 64,
)


async def bounded_hash_password(password: str) -> str:
    """Async threadpool wrapper for Argon2id hashing, bounded by a semaphore."""
    try:
        async with asyncio.timeout(2.0):
            await _HASH_SEMAPHORE.acquire()
    except TimeoutError:
        raise HTTPException(
            status_code=503,
            detail="Authentication engine saturated. Please retry shortly.",
        )

    try:
        return await run_in_threadpool(hash_password, password)
    finally:
        _HASH_SEMAPHORE.release()


async def bounded_verify_password(
    plain_password: str, stored_hash: str
) -> tuple[bool, str | None]:
    """Async threadpool wrapper for Argon2id verification, bounded by a semaphore."""
    try:
        async with asyncio.timeout(2.0):
            await _HASH_SEMAPHORE.acquire()
    except TimeoutError:
        raise HTTPException(
            status_code=503,
            detail="Authentication engine saturated. Please retry shortly.",
        )

    try:
        return await run_in_threadpool(
            verify_and_update_password, plain_password, stored_hash
        )
    finally:
        _HASH_SEMAPHORE.release()


async def bounded_verify_timing_sentinel(password: str) -> None:
    """Async threadpool wrapper for timing sentinels, bounded by a semaphore."""
    try:
        async with asyncio.timeout(2.0):
            await _HASH_SEMAPHORE.acquire()
    except TimeoutError:
        raise HTTPException(
            status_code=503,
            detail="Authentication engine saturated. Please retry shortly.",
        )

    try:
        return await run_in_threadpool(verify_timing_sentinel, password)
    finally:
        _HASH_SEMAPHORE.release()


# ---------------------------------------------------------------------------
# Verification code helpers
# ---------------------------------------------------------------------------


def generate_verification_code() -> str:
    """Generate a cryptographically secure 6-digit verification code.

    Uses ``secrets.randbelow`` to avoid modulo bias.  The result is
    zero-padded to always produce exactly 6 characters.
    """
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_verification_code(code: str, secret_key: bytes | None = None) -> str:
    """Return the HMAC-SHA256 hex digest of a verification code.

    The HMAC key defaults to ``JWT_SECRET_KEY`` from the environment.
    Hashing codes at rest prevents exposure if Valkey memory is dumped.
    """
    key = secret_key or _HMAC_KEY
    return hmac.new(key, code.encode("utf-8"), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Password reset token helpers
# ---------------------------------------------------------------------------


def generate_reset_token() -> tuple[str, str]:
    """Generate an opaque password-reset token with 256 bits of entropy.

    Returns:
        ``(raw_token, sha256_hex_hash)`` — the raw token is sent to the
        user via email; the SHA-256 hash is stored in Valkey as the
        lookup key.  This separation ensures the token value is never
        persisted in any data store.
    """
    raw = secrets.token_urlsafe(32)
    hashed = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, hashed
