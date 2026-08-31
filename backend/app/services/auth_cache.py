"""Authentication cache manager backed by Valkey (Redis-compatible).

Provides atomic operations for email verification codes and password
reset tokens using Lua scripts to eliminate TOCTOU race conditions.

Key namespace:
    email_verify:{email}             — pending verification code (JSON)
    email_verify_cooldown:{email}    — resend cooldown flag
    email_verify_rate:{email}        — hourly send rate (sorted set)
    password_reset:{sha256_hash}     — single-use reset token (JSON)
"""

from __future__ import annotations

import json
import logging
import time

from redis.asyncio import Redis
import redis.exceptions

logger = logging.getLogger("logsentinel.auth_cache")


class AuthCacheUnavailableError(Exception):
    """Raised when the underlying Valkey/Redis infrastructure is unreachable."""
    pass


def catch_redis_errors(func):
    """Decorator to catch RedisError and fail-closed by raising AuthCacheUnavailableError."""
    import functools
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except redis.exceptions.RedisError as e:
            logger.error("Valkey cache connection failed during %s: %s", func.__name__, e)
            raise AuthCacheUnavailableError("Authentication cache temporarily unavailable") from e
    return wrapper


# ---------------------------------------------------------------------------
# Lua Scripts — compiled once, executed atomically on the Valkey server
# ---------------------------------------------------------------------------

# Email verification: atomic verify-and-consume
_VERIFY_CODE_LUA = """
local data = redis.call('GET', KEYS[1])
if not data then return -1 end
local obj = cjson.decode(data)
if obj.attempts >= tonumber(ARGV[2]) then
    redis.call('DEL', KEYS[1])
    return -2
end
if obj.code_hash == ARGV[1] then
    redis.call('DEL', KEYS[1])
    return tonumber(obj.user_id)
end
obj.attempts = obj.attempts + 1
redis.call('SET', KEYS[1], cjson.encode(obj), 'KEEPTTL')
return -3
"""

# Password reset: atomic get-and-delete (single-use enforcement)
_CONSUME_RESET_TOKEN_LUA = """
local data = redis.call('GET', KEYS[1])
if not data then return nil end
redis.call('DEL', KEYS[1])
return data
"""


class AuthCacheManager:
    """Manages authentication state in Valkey with atomic Lua operations."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._verify_script: object | None = None
        self._consume_reset_script: object | None = None

    # ------------------------------------------------------------------
    # Script registration (lazy)
    # ------------------------------------------------------------------

    async def _get_verify_script(self):
        if self._verify_script is None:
            self._verify_script = self._redis.register_script(_VERIFY_CODE_LUA)
        return self._verify_script

    async def _get_consume_reset_script(self):
        if self._consume_reset_script is None:
            self._consume_reset_script = self._redis.register_script(
                _CONSUME_RESET_TOKEN_LUA
            )
        return self._consume_reset_script

    # ------------------------------------------------------------------
    # Email Verification
    # ------------------------------------------------------------------

    @catch_redis_errors
    async def store_verification_code(
        self,
        email: str,
        code_hash: str,
        user_id: int,
        ttl_seconds: int = 600,
    ) -> None:
        """Store a hashed verification code in Valkey with expiration.

        Any existing code for the same email is overwritten (new code
        replaces the old one on resend).
        """
        key = f"email_verify:{email.strip().lower()}"
        payload = json.dumps({
            "code_hash": code_hash,
            "attempts": 0,
            "user_id": user_id,
        })
        await self._redis.set(key, payload, ex=ttl_seconds)
        logger.debug("Stored verification code for email hash=%s", hash(email))

    @catch_redis_errors
    async def verify_code(
        self,
        email: str,
        submitted_code_hash: str,
        max_attempts: int = 5,
    ) -> int:
        """Atomically verify a submitted code.

        Returns:
            ``user_id`` (positive int) on success.
            ``-1`` if the key does not exist (expired or never stored).
            ``-2`` if maximum attempts exceeded (locked out).
            ``-3`` if the code does not match (wrong code).
        """
        key = f"email_verify:{email.strip().lower()}"
        script = await self._get_verify_script()
        result = await script(keys=[key], args=[submitted_code_hash, max_attempts])
        if isinstance(result, int):
            return result
        if result is None or result in (b"", ""):
            return -1
        return int(result)

    # ------------------------------------------------------------------
    # Password Reset Tokens
    # ------------------------------------------------------------------

    @catch_redis_errors
    async def store_reset_token(
        self,
        token_hash: str,
        user_id: int,
        ttl_seconds: int = 900,
    ) -> None:
        """Store a password-reset token hash with its associated user_id."""
        key = f"password_reset:{token_hash}"
        payload = json.dumps({"user_id": user_id})
        await self._redis.set(key, payload, ex=ttl_seconds)
        logger.debug("Stored password reset token (hash prefix=%s…)", token_hash[:8])

    @catch_redis_errors
    async def consume_reset_token(self, token_hash: str) -> int | None:
        """Atomically consume a password-reset token (single-use).

        Returns the ``user_id`` if the token existed, or ``None`` if
        the token was already used, expired, or never existed.
        """
        key = f"password_reset:{token_hash}"
        script = await self._get_consume_reset_script()
        result = await script(keys=[key])
        if isinstance(result, int):
            return result
        if result is None or result in (b"", ""):
            return None
        data = json.loads(result)
        return int(data["user_id"])

    # ------------------------------------------------------------------
    # Abuse Prevention — Resend Cooldown
    # ------------------------------------------------------------------

    @catch_redis_errors
    async def check_resend_cooldown(self, email: str) -> bool:
        """Return True if the email is still in cooldown (cannot resend)."""
        key = f"email_verify_cooldown:{email.strip().lower()}"
        return await self._redis.exists(key) > 0

    @catch_redis_errors
    async def set_resend_cooldown(self, email: str, ttl_seconds: int = 60) -> None:
        """Set a cooldown flag preventing immediate resends."""
        key = f"email_verify_cooldown:{email.strip().lower()}"
        await self._redis.set(key, "1", ex=ttl_seconds)

    @catch_redis_errors
    async def reserve_resend_cooldown(self, email: str, ttl_seconds: int = 60) -> bool:
        """Atomically set cooldown key if not exists (SET NX EX). Returns True if reserved."""
        key = f"email_verify_cooldown:{email.strip().lower()}"
        return bool(await self._redis.set(key, "1", ex=ttl_seconds, nx=True))

    @catch_redis_errors
    async def delete_cooldown(self, email: str) -> None:
        """Delete a cooldown flag explicitly on failure."""
        key = f"email_verify_cooldown:{email.strip().lower()}"
        await self._redis.delete(key)

    # ------------------------------------------------------------------
    # Abuse Prevention — Hourly Sliding Window Rate Limit
    # ------------------------------------------------------------------

    @catch_redis_errors
    async def check_hourly_rate(self, email: str, limit: int = 5) -> bool:
        """Return True if the email has exceeded the hourly send limit."""
        key = f"email_verify_rate:{email.strip().lower()}"
        now = time.time()
        window_start = now - 3600

        # Remove entries outside the 1-hour window
        await self._redis.zremrangebyscore(key, "-inf", window_start)

        count = await self._redis.zcard(key)
        return count >= limit

    @catch_redis_errors
    async def record_email_send(self, email: str, window_seconds: int = 3600) -> None:
        """Record an email send event in the sliding-window rate limiter."""
        import uuid
        key = f"email_verify_rate:{email.strip().lower()}"
        now = time.time()
        await self._redis.zadd(key, {f"{now}:{uuid.uuid4()}": now})
        await self._redis.expire(key, window_seconds)
