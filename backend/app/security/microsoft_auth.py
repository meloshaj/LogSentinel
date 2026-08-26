"""Microsoft Entra ID access-token verifier for LogSentinel.

This module validates Microsoft access tokens issued for a custom
LogSentinel API scope using the Microsoft v2.0 JWKS endpoint.  It is
NOT a generic OIDC ID-token verifier — it validates delegated API
access tokens carrying the ``scp`` (scope) claim.

Security invariants enforced:
    * RSA signature verified against Microsoft JWKS public keys.
    * ``alg=none`` is never accepted.
    * Signing key selected by ``kid`` from the JWKS.
    * Token expiration (``exp``) validated.
    * Not-before (``nbf``) validated when present.
    * Audience (``aud``) must exactly match the configured client_id.
    * Issuer (``iss``) must match the Microsoft v2.0 issuer template.
    * Tenant ID (``tid``) validated against an optional allow-list.
    * Required delegated scope (``scp``) validated.
    * Microsoft Graph tokens (aud=https://graph.microsoft.com) are
      explicitly rejected even if they carry valid signatures.
    * ``sub`` (not email) is the stable external identity key.
    * Raw tokens are never exposed in logs, responses, or exceptions.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import jwt
from jwt import PyJWKClient

from ..core.settings import MicrosoftAuthSettings

logger = logging.getLogger("logsentinel.microsoft_auth")

# Microsoft Graph audience — must never be accepted as a LogSentinel API token
_GRAPH_AUDIENCES = frozenset(
    {
        "https://graph.microsoft.com",
        "00000003-0000-0000-c000-000000000000",
    }
)

_CONSUMER_TENANT_ID = "9188040d-6c67-4c5b-b112-36a304b66dad"

# Multi-tenant issuer pattern: https://login.microsoftonline.com/{tenantid}/v2.0
_MULTI_TENANT_ISSUER_RE = re.compile(
    r"^https://login\.microsoftonline\.com/[0-9a-f-]+/v2\.0$"
)


class MicrosoftAuthError(Exception):
    """Base exception for Microsoft authentication failures.

    The ``error_code`` attribute carries a stable internal error label
    that is safe to include in HTTP responses.  The ``detail`` attribute
    carries a short human-readable explanation.  Neither value ever
    contains raw token data.
    """

    def __init__(self, error_code: str, detail: str) -> None:
        self.error_code = error_code
        self.detail = detail
        super().__init__(f"{error_code}: {detail}")


class MicrosoftAuthDisabledError(MicrosoftAuthError):
    def __init__(self) -> None:
        super().__init__(
            "microsoft_auth_disabled",
            "Microsoft authentication is not configured.",
        )


class InvalidMicrosoftTokenError(MicrosoftAuthError):
    def __init__(
        self, detail: str = "The Microsoft access token is invalid or expired."
    ) -> None:
        super().__init__("invalid_microsoft_token", detail)


class InvalidMicrosoftTenantError(MicrosoftAuthError):
    def __init__(self) -> None:
        super().__init__(
            "invalid_microsoft_tenant",
            "The token was issued by an untrusted tenant.",
        )


class MissingRequiredScopeError(MicrosoftAuthError):
    def __init__(self) -> None:
        super().__init__(
            "missing_required_scope",
            "The token does not include the required API scope.",
        )


class MicrosoftJWKSUnavailableError(MicrosoftAuthError):
    def __init__(self) -> None:
        super().__init__(
            "microsoft_jwks_unavailable",
            "Microsoft identity service is temporarily unreachable.",
        )


@dataclass(frozen=True)
class VerifiedMicrosoftIdentity:
    """Typed result of a successfully verified Microsoft access token.

    All fields are extracted from cryptographically verified claims.
    ``email`` and ``display_name`` are profile-only and must never be
    used as the identity lookup key.
    """

    subject: str
    """Audience-specific pairwise subject identifier (``sub`` claim)."""

    issuer: str
    """Token issuer URL (``iss`` claim)."""

    tenant_id: str
    """Microsoft Entra tenant ID (``tid`` claim)."""

    object_id: str | None = None
    """Microsoft directory object ID (``oid`` claim)."""

    email: str | None = None
    """Contact email (``email`` or ``preferred_username`` claim).  Informational only."""

    display_name: str | None = None
    """Display name (``name`` claim).  Informational only."""


class _CachedJWKClient:
    """Thread-safe wrapper around ``PyJWKClient`` with bounded TTL caching.

    ``PyJWKClient`` has its own internal cache, but this wrapper adds
    an explicit TTL boundary and safe replacement on cache expiry so that
    key rotation is honoured without fetching keys on every request.
    """

    def __init__(self, jwks_url: str, timeout: float, cache_ttl: int) -> None:
        self._jwks_url = jwks_url
        self._timeout = timeout
        self._cache_ttl = cache_ttl
        self._lock = threading.Lock()
        self._client: PyJWKClient | None = None
        self._created_at: float = 0.0

    def _ensure_client(self) -> PyJWKClient:
        now = time.monotonic()
        with self._lock:
            if self._client is None or (now - self._created_at) > self._cache_ttl:
                self._client = PyJWKClient(
                    self._jwks_url,
                    cache_keys=True,
                    lifespan=self._cache_ttl,
                    timeout=self._timeout,
                )
                self._created_at = now
            return self._client

    def get_signing_key_from_jwt(self, token: str) -> Any:
        """Retrieve the signing key for the given JWT ``kid``.

        Raises ``MicrosoftJWKSUnavailableError`` if the JWKS endpoint
        cannot be reached, or ``InvalidMicrosoftTokenError`` if the
        ``kid`` is not found in the key set.
        """
        client = self._ensure_client()
        try:
            return client.get_signing_key_from_jwt(token)
        except jwt.InvalidTokenError:
            raise InvalidMicrosoftTokenError("The Microsoft access token is malformed.")
        except jwt.PyJWKClientConnectionError:
            raise MicrosoftJWKSUnavailableError()
        except jwt.PyJWKClientError:
            raise InvalidMicrosoftTokenError(
                "Token signing key not found in Microsoft JWKS."
            )


class MicrosoftTokenVerifier:
    """Stateless (per-settings) verifier for Microsoft Entra access tokens.

    Create one instance per application lifetime (or per settings change)
    and call :meth:`verify` for each incoming token.
    """

    def __init__(self, settings: MicrosoftAuthSettings) -> None:
        self._settings = settings
        self._jwk_client = _CachedJWKClient(
            jwks_url=settings.jwks_url,
            timeout=settings.jwks_timeout_seconds,
            cache_ttl=settings.jwks_cache_ttl_seconds,
        )

    def verify(self, access_token: str) -> VerifiedMicrosoftIdentity:
        """Validate a Microsoft access token and return the verified identity.

        Raises a specific ``MicrosoftAuthError`` subclass on every
        failure mode.  The caller should map these to HTTP status codes.
        """
        if not self._settings.enabled:
            raise MicrosoftAuthDisabledError()

        # Reject malformed headers and unexpected algorithms before a token can
        # trigger a JWKS lookup or refresh.
        try:
            unverified_header = jwt.get_unverified_header(access_token)
        except jwt.InvalidTokenError:
            raise InvalidMicrosoftTokenError("The Microsoft access token is malformed.")
        if unverified_header.get("alg") != "RS256" or not isinstance(
            unverified_header.get("kid"), str
        ):
            raise InvalidMicrosoftTokenError(
                "The Microsoft access token header is invalid."
            )

        # ── Retrieve the signing key by kid ───────────────────────────
        signing_key = self._jwk_client.get_signing_key_from_jwt(access_token)

        # ── Decode and validate the token ─────────────────────────────
        try:
            payload = jwt.decode(
                access_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._settings.client_id,
                issuer=None,  # We validate issuer manually for multi-tenant
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_aud": True,
                    "require": ["sub", "iss", "aud", "exp", "tid"],
                },
            )
        except jwt.ExpiredSignatureError:
            raise InvalidMicrosoftTokenError("The Microsoft access token has expired.")
        except jwt.ImmatureSignatureError:
            raise InvalidMicrosoftTokenError(
                "The Microsoft access token is not yet valid."
            )
        except jwt.InvalidAudienceError:
            raise InvalidMicrosoftTokenError(
                "The token audience does not match the expected API."
            )
        except jwt.DecodeError:
            raise InvalidMicrosoftTokenError("The Microsoft access token is malformed.")
        except jwt.InvalidTokenError:
            raise InvalidMicrosoftTokenError()

        # ── Explicitly reject Microsoft Graph tokens ──────────────────
        token_aud = payload.get("aud", "")
        if token_aud in _GRAPH_AUDIENCES:
            raise InvalidMicrosoftTokenError(
                "Microsoft Graph tokens cannot be used for LogSentinel API authentication."
            )

        # ── Validate issuer ───────────────────────────────────────────
        iss = payload.get("iss", "")
        tid = payload.get("tid", "")

        if not isinstance(iss, str):
            raise InvalidMicrosoftTokenError("Token issuer claim is invalid.")

        if not isinstance(tid, str) or not tid:
            raise InvalidMicrosoftTokenError("Token is missing the tenant ID claim.")

        try:
            normalized_tid = str(UUID(tid))
        except (ValueError, TypeError, AttributeError):
            raise InvalidMicrosoftTokenError("Token tenant ID is not a valid GUID.")

        # Multi-tenant: issuer must be https://login.microsoftonline.com/{tid}/v2.0
        expected_issuer = f"https://login.microsoftonline.com/{normalized_tid}/v2.0"
        if iss != expected_issuer:
            # Also check the generic pattern for safety
            if not _MULTI_TENANT_ISSUER_RE.match(iss):
                raise InvalidMicrosoftTokenError(
                    "The token issuer is not a valid Microsoft identity endpoint."
                )
            raise InvalidMicrosoftTokenError(
                "The token issuer does not match the token tenant."
            )

        # ── Configured tenant mode and optional allow-list ────────────
        configured_tenant = self._settings.tenant_id
        if configured_tenant not in {"common", "organizations", "consumers"}:
            if normalized_tid != configured_tenant:
                raise InvalidMicrosoftTenantError()
        elif (
            configured_tenant == "consumers"
            and normalized_tid != _CONSUMER_TENANT_ID
            or configured_tenant == "organizations"
            and normalized_tid == _CONSUMER_TENANT_ID
        ):
            raise InvalidMicrosoftTenantError()

        if (
            self._settings.allowed_tenants
            and normalized_tid not in self._settings.allowed_tenants
        ):
            raise InvalidMicrosoftTenantError()

        # ── Validate required scope ───────────────────────────────────
        scp = payload.get("scp", "")
        scopes = set(scp.split()) if isinstance(scp, str) else set()
        if self._settings.required_scope not in scopes:
            raise MissingRequiredScopeError()

        # ── Extract stable identity ───────────────────────────────────
        sub = payload.get("sub", "")
        if not isinstance(sub, str) or not sub:
            raise InvalidMicrosoftTokenError("Token is missing the subject claim.")

        # ── Extract profile information (informational only) ──────────
        email: str | None = None
        raw_email = payload.get("email")
        if isinstance(raw_email, str) and "@" in raw_email:
            email = raw_email
        elif not email:
            preferred = payload.get("preferred_username")
            if isinstance(preferred, str) and "@" in preferred:
                email = preferred

        display_name = (
            payload.get("name") if isinstance(payload.get("name"), str) else None
        )
        oid = payload.get("oid") if isinstance(payload.get("oid"), str) else None

        return VerifiedMicrosoftIdentity(
            subject=sub,
            issuer=iss,
            tenant_id=normalized_tid,
            object_id=oid,
            email=email,
            display_name=display_name,
        )
