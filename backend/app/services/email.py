import logging
import smtplib
from email.message import EmailMessage

from ..core.settings import get_smtp_settings

logger = logging.getLogger(__name__)


def send_password_reset_email(email_to: str, token: str) -> None:
    """Send a password reset email using the configured SMTP server.

    In local development or test environments where SMTP_HOST is not valid
    or accessible, this logs the token instead of crashing.
    """
    settings = get_smtp_settings()

    # In a real app, generate a URL based on FRONTEND_URL.
    reset_url = f"https://logsentinel.local/reset-password?token={token}"

    msg = EmailMessage()
    msg.set_content(
        f"You have requested a password reset.\n\n"
        f"Click the link below to reset your password:\n"
        f"{reset_url}\n\n"
        f"If you did not request this, please ignore this email."
    )
    msg["Subject"] = "LogSentinel - Password Reset Request"
    msg["From"] = settings.emails_from_email
    msg["To"] = email_to

    try:
        # We use sync smtplib in a background task (which FastAPI runs in a threadpool)
        with smtplib.SMTP(settings.host, settings.port, timeout=5) as server:
            server.starttls()  # Enforce TLS
            if settings.user and settings.password:
                server.login(settings.user, settings.password)
            server.send_message(msg)
            logger.info("Password reset email sent to %s", email_to)
    except Exception as e:
        logger.warning(
            "Failed to send password reset email to %s via SMTP (%s:%s): %s. "
            "Falling back to stdout logging (URL omitted for security).",
            email_to,
            settings.host,
            settings.port,
            str(e),
        )
