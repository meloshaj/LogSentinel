"""Resilient Redis/Valkey connection pool initialization with exponential backoff."""

import asyncio
import logging
import os

from redis.asyncio import ConnectionPool, Redis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://valkey:6379/0")

_redis_pool: ConnectionPool | None = None

_MAX_RETRIES = 5
_BASE_DELAY_SECONDS = 1.0


async def init_redis_pool() -> Redis:
    """Initialize and return an asynchronous Redis client with a connection pool.

    Retries up to ``_MAX_RETRIES`` times with exponential backoff
    (1 s, 2 s, 4 s, 8 s, 16 s) to tolerate container startup ordering
    delays where Valkey may not yet be accepting connections.
    """
    global _redis_pool
    # Redact password for logging
    from urllib.parse import urlparse
    parsed = urlparse(REDIS_URL)
    safe_url = REDIS_URL
    if parsed.password:
        safe_url = REDIS_URL.replace(f":{parsed.password}@", ":***@")
    logger.info("Initializing Redis connection pool to %s", safe_url)

    last_error: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            _redis_pool = ConnectionPool.from_url(
                REDIS_URL,
                decode_responses=True,
                max_connections=100,
            )
            client = Redis(connection_pool=_redis_pool)
            await client.ping()
            logger.info(
                "Successfully connected to Redis on attempt %d/%d.",
                attempt,
                _MAX_RETRIES,
            )
            return client
        except Exception as exc:
            last_error = exc
            delay = _BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "Redis connection attempt %d/%d failed (%s: %s). "
                "Retrying in %.1fs...",
                attempt,
                _MAX_RETRIES,
                type(exc).__name__,
                exc,
                delay,
            )
            # Clean up the failed pool before retrying
            if _redis_pool is not None:
                try:
                    await _redis_pool.disconnect(inuse_connections=True)
                except Exception:
                    logger.debug("Failed to disconnect failed Redis pool during retry cleanup", exc_info=True)
                _redis_pool = None
            await asyncio.sleep(delay)

    raise RuntimeError(
        f"FATAL: Could not connect to Redis at {safe_url} after "
        f"{_MAX_RETRIES} attempts. Last error: {last_error}"
    )


async def close_redis_pool() -> None:
    """Close the Redis connection pool."""
    global _redis_pool
    if _redis_pool:
        logger.info("Closing Redis connection pool.")
        await _redis_pool.disconnect(inuse_connections=True)
        _redis_pool = None
