"""Focused contract tests for the PostgreSQL-authoritative reset boundary."""

from __future__ import annotations

import inspect
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from backend.app.core.orm import PasswordResetTokenRecord
from backend.app.routers.auth_router import reset_password
from backend.app.services.password_reset import (
    PasswordResetCompletion,
    PasswordResetInvalidError,
    complete_password_reset,
    digest_reset_token,
)


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value


class _Session:
    def __init__(self, *results):
        self.results = list(results)
        self.execute = AsyncMock(
            side_effect=lambda _statement: _Result(self.results.pop(0))
        )
        self.flush = AsyncMock()
        self.commit = AsyncMock()
        self.rollback = AsyncMock()


def test_digest_is_fixed_length_and_does_not_equal_raw_token() -> None:
    raw = "reset-token-test-value-with-sufficient-entropy"
    digest = digest_reset_token(raw)

    assert len(digest) == 64
    assert digest != raw
    assert digest == digest_reset_token(raw)


def test_route_does_not_use_valkey_for_reset_authorization() -> None:
    source = inspect.getsource(reset_password)

    assert "complete_password_reset" in source
    assert "_get_auth_cache" not in source
    assert "consume_reset_token" not in source


def test_failpoint_controls_are_inert_in_production() -> None:
    from backend.app.services.password_reset import _test_failpoint

    with patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "LOGSENTINEL_ALLOW_TEST_HOOKS": "1",
            "LOGSENTINEL_TEST_FAILPOINT": "before_transaction",
        },
        clear=False,
    ):
        _test_failpoint("before_transaction")


@pytest.mark.asyncio
async def test_completed_token_is_idempotent_without_hash_or_outbox_work() -> None:
    token = SimpleNamespace(state="completed")
    db = _Session(token)

    with (
        patch(
            "backend.app.services.password_reset.bounded_hash_password",
            new_callable=AsyncMock,
        ) as hash_password,
        patch(
            "backend.app.services.password_reset.enqueue_email",
            new_callable=AsyncMock,
        ) as enqueue,
    ):
        result = await complete_password_reset(
            db,
            token_digest="a" * 64,
            new_password="new-password-value",
        )

    assert result == PasswordResetCompletion(already_completed=True)
    hash_password.assert_not_awaited()
    enqueue.assert_not_awaited()
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_successful_transition_commits_token_password_and_outbox_together() -> (
    None
):
    now = datetime.now(timezone.utc)
    token = SimpleNamespace(
        state="issued",
        expires_at=now + timedelta(minutes=10),
        user_id=7,
        tenant_id="tenant-a",
        completed_at=None,
        expired_at=None,
        invalidated_at=None,
    )
    user = SimpleNamespace(
        id=7, tenant_id="tenant-a", email="user@example.com", status="active"
    )
    db = _Session(token, now, user, now)

    with (
        patch(
            "backend.app.services.password_reset.bounded_hash_password",
            new_callable=AsyncMock,
            return_value="argon2id-hash",
        ),
        patch(
            "backend.app.services.password_reset.UserRepository.update_password_with_timestamp",
            new_callable=AsyncMock,
        ) as update_password,
        patch(
            "backend.app.services.password_reset.enqueue_email",
            new_callable=AsyncMock,
        ) as enqueue,
    ):
        result = await complete_password_reset(
            db,
            token_digest="b" * 64,
            new_password="new-password-value",
        )

    assert result == PasswordResetCompletion(already_completed=False)
    assert token.state == "completed"
    update_password.assert_awaited_once()
    enqueue.assert_awaited_once()
    assert (
        enqueue.await_args.kwargs["idempotency_key"] == "password-changed:" + "b" * 64
    )
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()


def test_reset_model_has_no_plaintext_token_column() -> None:
    columns = set(PasswordResetTokenRecord.__table__.columns.keys())

    assert columns == {
        "token_digest",
        "user_id",
        "tenant_id",
        "issued_at",
        "expires_at",
        "state",
        "completed_at",
        "expired_at",
        "invalidated_at",
    }


@pytest.mark.asyncio
async def test_missing_token_fails_closed_without_mutation() -> None:
    db = _Session(None)

    with pytest.raises(PasswordResetInvalidError):
        await complete_password_reset(
            db,
            token_digest="c" * 64,
            new_password="new-password-value",
        )

    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()
