"""Regression coverage for secret-safe exception and diagnostic boundaries."""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.core import database as database_module
from backend.app.core import redis as redis_module
from backend.app.core.transaction import async_transactional
from backend.app.security.redaction import (
    MAX_SANITIZED_ERROR_LENGTH,
    sanitize_error_text,
)
from backend.app.workers.drain_worker import DrainWorker


SENTINELS = {
    "db_password": "SENTINEL_DB_PASSWORD",
    "valkey_password": "SENTINEL_VALKEY_PASSWORD",
    "auth_token": "SENTINEL_AUTH_TOKEN",
    "smtp_password": "SENTINEL_SMTP_PASSWORD",
    "s3_secret": "SENTINEL_S3_SECRET",
    "jwt_header": "SENTINEL_JWT_HEADER",
    "jwt_payload": "SENTINEL_JWT_PAYLOAD",
    "jwt_signature": "SENTINEL_JWT_SIGNATURE",
}


@pytest.mark.parametrize(
    "value",
    [
        "postgresql+asyncpg://db-user:SENTINEL_DB_PASSWORD@db.example:5432/logsentinel",
        "valkey://:SENTINEL_VALKEY_PASSWORD@valkey.example:6379/0",
        "Authorization: Bearer SENTINEL_AUTH_TOKEN",
        "smtp://smtp-user:SENTINEL_SMTP_PASSWORD@mail.example:587",
        "s3://access:SENTINEL_S3_SECRET@bucket.example/archive/object",
        "S3_SECRET_ACCESS_KEY=SENTINEL_S3_SECRET",
        "token=SENTINEL_AUTH_TOKEN&state=SENTINEL_STATE",
        "SENTINEL_JWT_HEADER.SENTINEL_JWT_PAYLOAD.SENTINEL_JWT_SIGNATURE",
    ],
)
def test_sanitizer_redacts_06d_sentinel_secret_corpus(value: str) -> None:
    safe = sanitize_error_text(value)

    assert "[REDACTED]" in safe
    assert all(sentinel not in safe for sentinel in SENTINELS.values())
    assert "SENTINEL_STATE" not in safe


def test_sanitizer_removes_controls_and_bounds_oversized_diagnostics() -> None:
    safe = sanitize_error_text(
        "before\r\nforged-entry\x00"
        " postgresql://user:SENTINEL_DB_PASSWORD@db.example/db " + "x" * 10000
    )

    assert "\r" not in safe
    assert "\n" not in safe
    assert "\x00" not in safe
    assert "SENTINEL_DB_PASSWORD" not in safe
    assert len(safe) <= MAX_SANITIZED_ERROR_LENGTH


@pytest.mark.asyncio
async def test_database_connectivity_failure_does_not_log_driver_secret(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class FailedConnection:
        async def __aenter__(self):
            raise RuntimeError(
                "connect failed for postgresql://db-user:SENTINEL_DB_PASSWORD@db.example/logsentinel"
            )

        async def __aexit__(self, *_args) -> None:
            return None

    class FailedEngine:
        def connect(self) -> FailedConnection:
            return FailedConnection()

    monkeypatch.setattr(database_module, "_engine", FailedEngine())
    monkeypatch.setattr(database_module, "_CONNECTIVITY_MAX_RETRIES", 1)
    monkeypatch.setattr(database_module, "_CONNECTIVITY_BASE_DELAY", 0)

    with caplog.at_level(logging.WARNING, logger="logsentinel.database"):
        with pytest.raises(RuntimeError, match="Could not connect") as raised:
            await database_module.verify_connectivity()

    assert "SENTINEL_DB_PASSWORD" not in caplog.text
    assert "SENTINEL_DB_PASSWORD" not in str(raised.value)


@pytest.mark.asyncio
async def test_valkey_connectivity_failure_does_not_log_driver_secret(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pool = MagicMock()
    pool.disconnect = AsyncMock()
    client = MagicMock()
    client.ping = AsyncMock(
        side_effect=RuntimeError(
            "connect failed for valkey://:SENTINEL_VALKEY_PASSWORD@valkey.example/0"
        )
    )

    monkeypatch.setattr(redis_module, "REDIS_URL", "valkey://valkey.example/0")
    monkeypatch.setattr(
        redis_module.ConnectionPool, "from_url", MagicMock(return_value=pool)
    )
    monkeypatch.setattr(redis_module, "Redis", MagicMock(return_value=client))
    monkeypatch.setattr(redis_module, "_MAX_RETRIES", 1)
    monkeypatch.setattr(redis_module, "_BASE_DELAY_SECONDS", 0)

    with caplog.at_level(logging.WARNING, logger=redis_module.logger.name):
        with pytest.raises(RuntimeError, match="Could not connect") as raised:
            await redis_module.init_redis_pool()

    assert "SENTINEL_VALKEY_PASSWORD" not in caplog.text
    assert "SENTINEL_VALKEY_PASSWORD" not in str(raised.value)


@pytest.mark.asyncio
async def test_transaction_rollback_does_not_log_exception_secret(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    with caplog.at_level(logging.ERROR, logger="logsentinel.transaction"):
        with pytest.raises(RuntimeError, match="SENTINEL_SMTP_PASSWORD"):
            async with async_transactional(session):
                raise RuntimeError(
                    "smtp delivery failed smtp://user:SENTINEL_SMTP_PASSWORD@mail.example/"
                )

    assert "SENTINEL_SMTP_PASSWORD" not in caplog.text
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_drain_retry_log_sanitizes_caller_supplied_error_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = DrainWorker(log_buffer=None, parser=MagicMock(), max_retries=2)

    with caplog.at_level(logging.ERROR, logger="logsentinel.drain_worker"):
        outcome = await worker._retry_or_route_stream_failure(
            message_id="message-1",
            raw_payload="payload",
            error_traceback="Traceback: s3://access:SENTINEL_S3_SECRET@bucket/object",
            error_message="provider failed Authorization: Bearer SENTINEL_AUTH_TOKEN",
        )

    assert outcome.value == "retryable_failure"
    assert "SENTINEL_S3_SECRET" not in caplog.text
    assert "SENTINEL_AUTH_TOKEN" not in caplog.text
    assert all(
        sentinel not in json.dumps(record.__dict__, default=str)
        for record in caplog.records
        for sentinel in ("SENTINEL_S3_SECRET", "SENTINEL_AUTH_TOKEN")
    )


@pytest.mark.asyncio
async def test_drain_dlq_redacts_traceback_and_metadata_before_xadd() -> None:
    class FakeRedis:
        def __init__(self) -> None:
            self.entry: dict[str, object] | None = None

        async def xadd(self, _stream, entry, **_kwargs):
            self.entry = entry
            return "dlq-1"

        async def expire(self, _stream, _seconds):
            return True

    fake_redis = FakeRedis()
    worker = DrainWorker(log_buffer=None, parser=MagicMock())
    worker.set_redis_client(fake_redis)  # type: ignore[arg-type]

    await worker._forward_to_dlq(
        raw_payload="Authorization: Bearer SENTINEL_AUTH_TOKEN",
        error_traceback=(
            "Traceback: smtp://user:SENTINEL_SMTP_PASSWORD@mail.example/ "
            "s3://access:SENTINEL_S3_SECRET@bucket/object"
        ),
        log_id="log-1",
        metadata={
            "raw_message": "SENTINEL_JWT_HEADER.SENTINEL_JWT_PAYLOAD.SENTINEL_JWT_SIGNATURE"
        },
    )

    assert fake_redis.entry is not None
    serialized = json.dumps(fake_redis.entry, default=str)
    assert all(sentinel not in serialized for sentinel in SENTINELS.values())
