"""Transactional email dispatcher for LogSentinel authentication flows.

Provides functions for verification emails, password resets, and
password-change notifications.  All functions use synchronous smtplib
in FastAPI BackgroundTasks (which runs them in a thread pool).

Security invariants:
    * Plaintext codes and tokens are NEVER logged.
    * Recipient email addresses are logged as truncated hashes.
    * SMTP failures are caught and logged — they never crash the caller.
    * Transient transport failures retry up to 3 times with exponential
      backoff; permanent SMTP/configuration failures stop immediately.
"""

from __future__ import annotations

import hashlib
import html
import logging
import os
import smtplib
import socket
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr

from ..core.settings import get_smtp_settings

logger = logging.getLogger("logsentinel.email")

_MAX_RETRIES = 3
_BACKOFF_BASE = 2  # seconds


def _email_hash(email: str) -> str:
    """Return a truncated SHA-256 hash of an email for safe logging."""
    return hashlib.sha256(email.lower().encode()).hexdigest()[:12]


def _frontend_url() -> str:
    """Return a validated trusted frontend origin for reset links."""
    urls = os.getenv("FRONTEND_URL", "http://localhost:8080")
    candidate = urls.split(",")[0].strip().rstrip("/")
    from urllib.parse import urlsplit

    parsed = urlsplit(candidate)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("FRONTEND_URL is not a valid trusted origin")
    if (
        os.getenv("ENVIRONMENT", "development").strip().lower() == "production"
        and parsed.scheme != "https"
    ):
        raise ValueError("FRONTEND_URL must use HTTPS in production")
    return candidate


def _is_retryable(exc: Exception) -> bool:
    """Retry transport failures, but stop immediately on permanent errors."""
    if isinstance(
        exc,
        (
            smtplib.SMTPAuthenticationError,
            smtplib.SMTPNotSupportedError,
            smtplib.SMTPHeloError,
            smtplib.SMTPRecipientsRefused,
            ValueError,
        ),
    ):
        return False
    if isinstance(exc, smtplib.SMTPResponseException) and exc.smtp_code >= 500:
        return False
    return isinstance(
        exc,
        (smtplib.SMTPException, OSError, socket.timeout, TimeoutError, ssl.SSLError),
    )


def _send_with_retry(msg: EmailMessage, recipient_hash: str) -> None:
    """Send an email with retry logic and exponential backoff.

    This is a synchronous function intended to run in a background
    thread (via FastAPI BackgroundTasks).
    """
    settings = get_smtp_settings()
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            with smtplib.SMTP(settings.host, settings.port, timeout=10) as server:
                # Use STARTTLS when not connecting to a local dev server
                if settings.port != 1025:
                    server.starttls()
                if settings.user and settings.password:
                    server.login(settings.user, settings.password)
                server.send_message(msg)
                logger.info(
                    "Email sent to %s… (attempt %d/%d)",
                    recipient_hash,
                    attempt,
                    _MAX_RETRIES,
                )
                return
        except Exception as exc:
            logger.warning(
                "Email dispatch attempt %d/%d failed for recipient=%s (%s; retryable=%s)",
                attempt,
                _MAX_RETRIES,
                recipient_hash,
                type(exc).__name__,
                _is_retryable(exc),
            )
            if _is_retryable(exc) and attempt < _MAX_RETRIES:
                delay = _BACKOFF_BASE**attempt
                time.sleep(delay)
            else:
                break

    logger.error(
        "Failed to send email to recipient=%s after %d attempts",
        recipient_hash,
        attempt,
    )


# ---------------------------------------------------------------------------
# Email Verification
# ---------------------------------------------------------------------------


def send_verification_email(email_to: str, code: str) -> None:
    """Send a 6-digit email verification code.

    The code is embedded in both HTML and plaintext parts.
    """
    settings = get_smtp_settings()
    recipient_hash = _email_hash(email_to)

    plain_body = (
        f"Welcome to LogSentinel!\n\n"
        f"Your email verification code is:\n\n"
        f"    {code}\n\n"
        f"This code expires in 10 minutes.\n\n"
        f"If you did not create a LogSentinel account, please ignore this email.\n"
    )

    html_body = f"""\
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 24px;">
  <h2 style="color: #1a1a2e;">Welcome to LogSentinel</h2>
  <p>Your email verification code is:</p>
  <div style="background: #f0f4ff; border-radius: 8px; padding: 20px; text-align: center; margin: 24px 0;">
    <span style="font-size: 32px; font-weight: 700; letter-spacing: 8px; color: #1a1a2e;">{code}</span>
  </div>
  <p style="color: #666; font-size: 14px;">This code expires in <strong>10 minutes</strong>.</p>
  <hr style="border: none; border-top: 1px solid #eee; margin: 24px 0;">
  <p style="color: #999; font-size: 12px;">If you did not create a LogSentinel account, please ignore this email.</p>
</body>
</html>
"""

    msg = EmailMessage()
    msg["Subject"] = "LogSentinel — Verify your email address"
    msg["From"] = formataddr((settings.emails_from_name, settings.emails_from_email))
    msg["To"] = email_to
    msg.set_content(plain_body)
    msg.add_alternative(html_body, subtype="html")

    logger.info("Dispatching verification email to %s…", recipient_hash)
    _send_with_retry(msg, recipient_hash)


# ---------------------------------------------------------------------------
# Password Reset
# ---------------------------------------------------------------------------


def send_password_reset_email(email_to: str, token: str) -> None:
    """Send a password reset email containing a single-use reset link.

    The ``token`` is the raw opaque token (not the hash).
    """
    settings = get_smtp_settings()
    recipient_hash = _email_hash(email_to)
    frontend = _frontend_url()
    from urllib.parse import quote

    reset_url = f"{frontend}/reset-password?token={quote(token, safe='')}"
    escaped_reset_url = html.escape(reset_url, quote=True)

    plain_body = (
        f"You have requested a password reset for your LogSentinel account.\n\n"
        f"Click the link below to reset your password:\n"
        f"{reset_url}\n\n"
        f"This link expires in 15 minutes and can only be used once.\n\n"
        f"If you did not request this, please ignore this email. "
        f"Your password will remain unchanged.\n"
    )

    html_body = f"""\
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 24px;">
  <h2 style="color: #1a1a2e;">Password Reset Request</h2>
  <p>You have requested a password reset for your LogSentinel account.</p>
  <div style="text-align: center; margin: 24px 0;">
  <a href="{escaped_reset_url}" style="background: #4f46e5; color: white; padding: 12px 32px; border-radius: 6px; text-decoration: none; font-weight: 600;">Reset Password</a>
  </div>
  <p style="color: #666; font-size: 14px;">This link expires in <strong>15 minutes</strong> and can only be used once.</p>
  <hr style="border: none; border-top: 1px solid #eee; margin: 24px 0;">
  <p style="color: #999; font-size: 12px;">If you did not request this, please ignore this email. Your password will remain unchanged.</p>
</body>
</html>
"""

    msg = EmailMessage()
    msg["Subject"] = "LogSentinel — Password Reset Request"
    msg["From"] = formataddr((settings.emails_from_name, settings.emails_from_email))
    msg["To"] = email_to
    msg.set_content(plain_body)
    msg.add_alternative(html_body, subtype="html")

    logger.info("Dispatching password reset email to %s…", recipient_hash)
    _send_with_retry(msg, recipient_hash)


# ---------------------------------------------------------------------------
# Password Changed Notification
# ---------------------------------------------------------------------------


def send_password_changed_notification(email_to: str) -> None:
    """Send a security notification that the user's password was changed.

    This alert helps legitimate users detect unauthorized password resets.
    """
    settings = get_smtp_settings()
    recipient_hash = _email_hash(email_to)

    plain_body = (
        "Your LogSentinel account password was recently changed.\n\n"
        "If you made this change, no further action is needed.\n\n"
        "If you did NOT change your password, please contact support "
        "immediately as your account may be compromised.\n"
    )

    html_body = """\
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 24px;">
  <h2 style="color: #1a1a2e;">Password Changed</h2>
  <p>Your LogSentinel account password was recently changed.</p>
  <p>If you made this change, no further action is needed.</p>
  <div style="background: #fff3cd; border-left: 4px solid #ffc107; padding: 12px 16px; margin: 24px 0; border-radius: 4px;">
    <strong>Did not make this change?</strong><br>
    <span style="font-size: 14px;">Please contact support immediately as your account may be compromised.</span>
  </div>
  <hr style="border: none; border-top: 1px solid #eee; margin: 24px 0;">
  <p style="color: #999; font-size: 12px;">This is an automated security notification from LogSentinel.</p>
</body>
</html>
"""

    msg = EmailMessage()
    msg["Subject"] = "LogSentinel — Your password was changed"
    msg["From"] = formataddr((settings.emails_from_name, settings.emails_from_email))
    msg["To"] = email_to
    msg.set_content(plain_body)
    msg.add_alternative(html_body, subtype="html")

    logger.info("Dispatching password-changed notification to %s…", recipient_hash)
    _send_with_retry(msg, recipient_hash)
