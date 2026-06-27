"""Localized async transaction manager for LogSentinel.

Provides two complementary patterns for wrapping database operations in
atomic transactions:

1. **Context manager** — ``async_transactional(session)``
2. **Decorator** — ``@transactional`` on an async function whose first
   positional argument is an ``AsyncSession``.

Both guarantee:
- ``session.commit()`` on success.
- ``session.rollback()`` + structured logging on failure.
- The original exception is always re-raised to avoid silent data loss.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any, ParamSpec, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("logsentinel.transaction")

P = ParamSpec("P")
R = TypeVar("R")

# ---------------------------------------------------------------------------
# Context Manager
# ---------------------------------------------------------------------------


@asynccontextmanager
async def async_transactional(
    session: AsyncSession,
) -> AsyncGenerator[AsyncSession, None]:
    """Wrap a block of async database operations in an atomic transaction.

    Usage::

        async with async_transactional(session) as txn:
            txn.add(some_model)
            # commit happens automatically on clean exit

    On exception the session is rolled back, the failure is logged with full
    context, and the exception is re-raised.
    """
    try:
        yield session
        await session.commit()
    except Exception as exc:
        await session.rollback()
        logger.error(
            "Transaction rolled back — %s: %s",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        raise


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def transactional(
    func: Callable[..., Any],
) -> Callable[..., Any]:
    """Decorator that wraps an async function in an atomic transaction.

    The decorated function **must** accept an ``AsyncSession`` as its first
    positional argument.  The decorator commits after a successful return and
    rolls back + logs on any exception.

    Usage::

        @transactional
        async def create_incident(session: AsyncSession, data: dict) -> Incident:
            incident = Incident(**data)
            session.add(incident)
            return incident  # commit is automatic
    """

    @functools.wraps(func)
    async def wrapper(session: AsyncSession, *args: Any, **kwargs: Any) -> Any:
        try:
            result = await func(session, *args, **kwargs)
            await session.commit()
            return result
        except Exception as exc:
            await session.rollback()
            logger.error(
                "Transaction rolled back in %s — %s: %s",
                func.__qualname__,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            raise

    return wrapper
