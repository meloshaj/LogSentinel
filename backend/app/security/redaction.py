"""Central, deterministic redaction helpers for logs and durable diagnostics."""

from __future__ import annotations
import re
from typing import Any

MAX_SANITIZED_ERROR_LENGTH = 2048
REDACTED = "[REDACTED]"
_SECRET_KEY_PARTS = {
    "password",
    "passwd",
    "secret",
    "token",
    "session_token",
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "refresh_token",
    "id_token",
    "client_secret",
    "smtp_password",
    "smtp_pass",
    "s3_access_key_id",
    "s3_secret_access_key",
    "aws_access_key_id",
    "aws_secret_access_key",
}
_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_URL_PATTERN = re.compile(
    r"(?i)\b(?:https?|postgres(?:ql)?(?:\+asyncpg)?|redis(?:s)?|valkey|smtp(?:s)?|s3)://[^\s<>'\"]+"
)
_AUTH_PATTERN = re.compile(r"(?i)\b(authorization\s*:\s*)?(bearer|basic)\s+[^\s,;]+")
_JWT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}(?![A-Za-z0-9_-])"
)
_QUERY_SECRET_PATTERN = re.compile(
    r"(?i)(\b(?:access_token|refresh_token|id_token|code|state|password|passwd|secret|api[_-]?key|client_secret|smtp_password|s3_access_key_id|s3_secret_access_key|aws_access_key_id|aws_secret_access_key)\s*[=:]\s*)([^\s&,;]+)"
)
_NAMED_SECRET_PATTERN = re.compile(
    r"(?i)(\b(?:smtp_password|smtp_pass|password|passwd|secret|token|session_token|api[_-]?key|authorization|client_secret|s3_access_key_id|s3_secret_access_key|aws_access_key_id|aws_secret_access_key)\s*[:=]\s*)([^\s,;]+)"
)
_LOGSENTINEL_KEY_PATTERN = re.compile(r"\blsn_(?:live|test)_[A-Za-z0-9_-]+\b")


def _redact_url(match: re.Match[str]) -> str:
    return f"{match.group(0).split(':', 1)[0].lower()}://{REDACTED}"


def sanitize_error_text(
    value: object, *, maximum_length: int = MAX_SANITIZED_ERROR_LENGTH
) -> str:
    """Return a single-line, secret-redacted, bounded diagnostic string."""
    text = _CONTROL_PATTERN.sub(" ", str(value))
    text = _URL_PATTERN.sub(_redact_url, text)
    text = _AUTH_PATTERN.sub(
        lambda m: f"{m.group(1) or ''}{m.group(2)} {REDACTED}", text
    )
    text = _JWT_PATTERN.sub(REDACTED, text)
    text = _LOGSENTINEL_KEY_PATTERN.sub(REDACTED, text)
    text = _QUERY_SECRET_PATTERN.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    text = _NAMED_SECRET_PATTERN.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    text = " ".join(text.split())
    if maximum_length < 1:
        return ""
    if len(text) > maximum_length:
        suffix = "...[TRUNCATED]"
        return text[: max(0, maximum_length - len(suffix))] + suffix
    return text


def sanitize_exception_text(
    exc: BaseException, *, maximum_length: int = MAX_SANITIZED_ERROR_LENGTH
) -> str:
    """Return bounded safe context for an exception without its raw payload."""
    return sanitize_error_text(
        f"{type(exc).__name__}: {exc}", maximum_length=maximum_length
    )


def redact_text(value: str, *, maximum_length: int = MAX_SANITIZED_ERROR_LENGTH) -> str:
    return sanitize_error_text(value, maximum_length=maximum_length)


def redact_value(value: Any, *, maximum_depth: int = 8, _depth: int = 0) -> Any:
    if _depth > maximum_depth:
        return "[REDACTED_DEPTH]"
    if isinstance(value, str):
        return sanitize_error_text(value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key)
            normalized = key_text.lower().replace("-", "_")
            if any(part in normalized for part in _SECRET_KEY_PARTS) or normalized in {
                "raw_message",
                "raw",
                "authorization_header",
            }:
                result[key_text] = REDACTED
            else:
                result[key_text] = redact_value(
                    child, maximum_depth=maximum_depth, _depth=_depth + 1
                )
        return result
    if isinstance(value, (list, tuple)):
        return [
            redact_value(child, maximum_depth=maximum_depth, _depth=_depth + 1)
            for child in value
        ]
    return value
