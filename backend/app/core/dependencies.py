"""Shared FastAPI dependency providers for LogSentinel.

Centralises access to application-scoped singletons (Redis/Valkey client,
database sessions, etc.) so that routers never instantiate their own
connection pools.
"""

from __future__ import annotations

from fastapi import Request
from redis.asyncio import Redis


async def get_redis_client(request: Request) -> Redis:
    """Return the shared Redis/Valkey client from application state.

    The client is initialised once during the FastAPI ``lifespan`` startup
    handler and stored on ``app.state.redis``.  This dependency simply
    forwards that singleton so route handlers avoid creating throw-away
    connection pools.
    """
    return request.app.state.redis
