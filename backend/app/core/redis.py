import logging
import os

from redis.asyncio import ConnectionPool, Redis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://valkey:6379/0")

_redis_pool: ConnectionPool | None = None

async def init_redis_pool() -> Redis:
    """Initialize and return an asynchronous Redis client with a connection pool."""
    global _redis_pool
    logger.info("Initializing Redis connection pool to %s", REDIS_URL)
    _redis_pool = ConnectionPool.from_url(
        REDIS_URL,
        decode_responses=True,
        max_connections=100
    )
    client = Redis(connection_pool=_redis_pool)
    
    # Verify connection
    try:
        await client.ping()
        logger.info("Successfully connected to Redis.")
    except Exception as e:
        logger.error("Failed to connect to Redis: %s", str(e))
    
    return client

async def close_redis_pool() -> None:
    """Close the Redis connection pool."""
    global _redis_pool
    if _redis_pool:
        logger.info("Closing Redis connection pool.")
        await _redis_pool.disconnect(inuse_connections=True)
        _redis_pool = None
