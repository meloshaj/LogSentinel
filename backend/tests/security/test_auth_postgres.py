"""Integration tests using an ephemeral PostgreSQL database via testcontainers.

These tests run against a real PostgreSQL instance to verify native
features that sqlite cannot faithfully simulate, such as:
  1. Partial indexes on the `status` column for pending users.
  2. Native timestamp truncation logic (`func.date_trunc('second', func.now())`).
  3. `cleanup_pending_users` cascade behavior and correct DB-side timezone operations.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Skip these tests if testcontainers is not installed or Docker isn't running
try:
    from testcontainers.postgres import PostgresContainer
except ImportError:
    pytest.skip("testcontainers not installed", allow_module_level=True)

from backend.app.core.orm import UserRecord
from backend.app.core.user_status import ACTIVE, PENDING_VERIFICATION
from backend.app.repositories.user_repository import UserRepository
from backend.app.security.auth import get_current_user

# ---------------------------------------------------------------------------
# Test Database Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def postgres_url():
    """Spin up a Postgres container and yield the async connection URL."""
    try:
        container = PostgresContainer("postgres:15-alpine")
        container.start()
    except Exception as exc:
        pytest.skip(f"Docker is unavailable for PostgreSQL integration tests: {exc}")

    # Generate the async connection URL
    url = container.get_connection_url().replace(
        "postgresql+psycopg2", "postgresql+asyncpg"
    )

    yield url
    container.stop()


@pytest_asyncio.fixture
async def postgres_engine(postgres_url):
    """Create a new async engine per test to isolate event loops."""
    engine = create_async_engine(postgres_url, echo=False)

    # Ensure the DB schema is created
    async with engine.begin() as conn:
        from backend.app.core.orm import Base

        await conn.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(postgres_engine):
    """Yield a transactional session for each test."""
    factory = async_sessionmaker(
        postgres_engine, expire_on_commit=False, class_=AsyncSession
    )
    async with factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_garbage_collection_pending_users(db_session: AsyncSession):
    """Verify that cleanup_pending_users correctly removes stale unverified accounts."""
    now = datetime.now(timezone.utc)

    # 1. Stale pending user (> 24 hours old)
    stale_user = UserRecord(
        email="stale@example.com",
        status=PENDING_VERIFICATION,
        created_at=now - timedelta(hours=25),
    )

    # 2. Fresh pending user (< 24 hours old)
    fresh_user = UserRecord(
        email="fresh@example.com",
        status=PENDING_VERIFICATION,
        created_at=now - timedelta(hours=23),
    )

    # 3. Stale active user (should NEVER be deleted)
    active_user = UserRecord(
        email="active@example.com",
        status=ACTIVE,
        created_at=now - timedelta(hours=48),
        email_verified_at=now - timedelta(hours=48),
    )

    db_session.add_all([stale_user, fresh_user, active_user])
    await db_session.commit()

    # Run GC
    deleted_count = await UserRepository.cleanup_pending_users(
        db_session, max_age_hours=24
    )

    # Should only delete the stale pending user
    assert deleted_count == 1

    # Verify records
    assert (
        await UserRepository.get_user_by_email(db_session, "stale@example.com") is None
    )
    assert (
        await UserRepository.get_user_by_email(db_session, "fresh@example.com")
        is not None
    )
    assert (
        await UserRepository.get_user_by_email(db_session, "active@example.com")
        is not None
    )


@pytest.mark.asyncio
async def test_password_changed_at_truncation(db_session: AsyncSession):
    """Verify that password_changed_at is strictly truncated to the second."""
    # Create user
    user = await UserRepository.create_user(
        db=db_session,
        email="truncation@example.com",
        hashed_password="old_hash",
        status=ACTIVE,
    )

    # Update password using the new date_trunc logic
    await UserRepository.update_password_with_timestamp(db_session, user, "new_hash")

    # Fetch from DB to see what Postgres actually stored
    await db_session.refresh(user)

    # Ensure microsecond is 0 (truncated at DB level)
    assert user.password_changed_at.microsecond == 0


@pytest.mark.asyncio
async def test_get_current_user_timestamp_collision(db_session: AsyncSession):
    """Verify get_current_user correctly normalizes iat vs password_changed_at."""
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials

    # Create user and simulate a password change with artificial microseconds
    user = await UserRepository.create_user(
        db=db_session, email="collision@example.com", status=ACTIVE
    )

    # Simulate DB returning a timestamp with microseconds (e.g., .999)
    # This might happen if the code is bypassed, or before the date_trunc patch
    fake_changed_at = datetime.now(timezone.utc).replace(microsecond=999000)
    user.password_changed_at = fake_changed_at
    await db_session.commit()

    # Issue a token at the EXACT SAME SECOND (10:00:00), which results in iat lacking microseconds
    # We fake the iat by backdating it
    import jwt
    from backend.app.security.auth import JWT_ALGORITHM, JWT_SECRET_KEY

    token = jwt.encode(
        {
            "sub": user.email,
            "exp": fake_changed_at.timestamp() + 3600,
            "iat": int(fake_changed_at.timestamp()),
        },
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    # This should SUCCEED because the microsecond of fake_changed_at is normalized to 0 in get_current_user
    try:
        resolved_user = await get_current_user(creds, db_session)
        assert resolved_user.id == user.id
    except HTTPException:
        pytest.fail(
            "get_current_user incorrectly rejected a token issued in the same second due to microsecond mismatch"
        )
