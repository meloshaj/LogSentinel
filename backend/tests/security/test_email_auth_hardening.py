"""Focused regression tests for the production email-authentication controls."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from email.message import EmailMessage

import pytest
from fastapi import HTTPException, Request

from backend.app.core.email_identity import canonicalize_email
from backend.app.core.settings import SMTPSettings, validate_auth_email_configuration
from backend.app.core.user_status import SUSPENDED
from backend.app.routers.auth_router import UserLoginRequest, login_user
from backend.app.services import auth_cache, email


def test_email_identity_is_canonical_at_every_boundary() -> None:
    assert canonicalize_email("  Alice@Example.COM ") == "alice@example.com"


def test_permanent_smtp_errors_are_not_retried() -> None:
    auth_error = email.smtplib.SMTPAuthenticationError(535, b"rejected")
    assert email._is_retryable(auth_error) is False
    assert email._is_retryable(ValueError("bad configuration")) is False
    assert email._is_retryable(email.smtplib.SMTPServerDisconnected()) is True


def test_reset_email_escapes_and_does_not_log_raw_token(monkeypatch, caplog) -> None:
    settings = SMTPSettings(
        host="smtp.example.test",
        port=587,
        user="mailer",
        password="test-only",
        emails_from_email="noreply@example.test",
    )
    sent: list[EmailMessage] = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self):
            pass

        def login(self, *args):
            pass

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setenv("FRONTEND_URL", "https://app.example.test")
    monkeypatch.setattr(email, "get_smtp_settings", lambda: settings)
    monkeypatch.setattr(email.smtplib, "SMTP", FakeSMTP)

    raw_token = "token&must-not-appear-in-logs"
    email.send_password_reset_email("Alice@Example.COM", raw_token)

    assert len(sent) == 1
    message = sent[0]
    assert (
        "token%26must-not-appear-in-logs"
        in message.get_body(preferencelist=("plain",)).get_content()
    )
    assert (
        "token%26must-not-appear-in-logs"
        in message.get_body(preferencelist=("html",)).get_content()
    )
    assert raw_token not in caplog.text


@pytest.mark.parametrize(
    ("environment", "frontend", "smtp_host", "smtp_user", "smtp_password"),
    [
        ("production", "http://app.example.test", "smtp.example.test", "u", "p"),
        ("production", "https://app.example.test", "localhost", "u", "p"),
        ("production", "https://app.example.test", "smtp.example.test", "", "p"),
    ],
)
def test_production_email_configuration_fails_closed(
    monkeypatch,
    environment,
    frontend,
    smtp_host,
    smtp_user,
    smtp_password,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("FRONTEND_URL", frontend)
    monkeypatch.setenv("SMTP_HOST", smtp_host)
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", smtp_user)
    monkeypatch.setenv("SMTP_PASSWORD", smtp_password)
    monkeypatch.setenv("EMAILS_FROM_EMAIL", "noreply@example.test")
    with pytest.raises(RuntimeError):
        validate_auth_email_configuration()


@pytest.mark.asyncio
async def test_suspended_password_login_never_issues_a_token() -> None:
    user = MagicMock(status=SUSPENDED, hashed_password="unused")
    request = Request(
        scope={
            "type": "http",
            "method": "POST",
            "headers": [],
            "client": ("127.0.0.1", 8000),
            "path": "/api/auth/login",
        }
    )
    with (
        patch(
            "backend.app.routers.auth_router.UserRepository.get_user_by_email",
            new=AsyncMock(return_value=user),
        ),
        patch(
            "backend.app.routers.auth_router.bounded_verify_timing_sentinel",
            new=AsyncMock(),
        ),
        patch("backend.app.routers.auth_router.create_access_token") as create_token,
    ):
        with pytest.raises(HTTPException) as exc:
            await login_user(
                request,
                UserLoginRequest(email=" Alice@Example.COM ", password="password"),
                AsyncMock(),
            )
    assert exc.value.status_code == 403
    create_token.assert_not_called()


def test_atomic_email_rate_limiter_is_server_side_script() -> None:
    assert "ZCARD" in auth_cache._RESERVE_EMAIL_SEND_LUA
    assert "ZADD" in auth_cache._RESERVE_EMAIL_SEND_LUA
    assert "EXPIRE" in auth_cache._RESERVE_EMAIL_SEND_LUA
