"""Deterministic tests for Microsoft Entra ID authentication.

Tests cover token verification, identity resolution, account provisioning,
and all documented failure modes without making any live internet calls.

Test RSA key material is generated at module load time for deterministic
token construction.  No real provider tokens or credentials are used.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import status
from fastapi.testclient import TestClient

from backend.app.core.database import get_async_session
from backend.app.core.orm import ExternalIdentityRecord, UserRecord
from backend.app.main import app
from backend.app.routers import auth_router as _auth_router_module
from backend.app.security.auth import create_access_token
from backend.app.security.microsoft_auth import (
    InvalidMicrosoftTenantError,
    InvalidMicrosoftTokenError,
    MicrosoftAuthDisabledError,
    MicrosoftAuthError,
    MicrosoftJWKSUnavailableError,
    MicrosoftTokenVerifier,
    MissingRequiredScopeError,
    VerifiedMicrosoftIdentity,
)
from backend.app.core.settings import MicrosoftAuthSettings


# ─── Test RSA Key Material ───────────────────────────────────────────────────

_TEST_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_TEST_RSA_PRIVATE_PEM = _TEST_RSA_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)
_TEST_RSA_PUBLIC_KEY = _TEST_RSA_KEY.public_key()

# A second key for "wrong key" tests
_WRONG_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_WRONG_RSA_PRIVATE_PEM = _WRONG_RSA_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)

# ─── Test Constants ──────────────────────────────────────────────────────────

_TEST_CLIENT_ID = "test-client-id-00000000"
_TEST_TENANT_ID = "test-tenant-id-11111111"
_TEST_ISSUER = f"https://login.microsoftonline.com/{_TEST_TENANT_ID}/v2.0"
_TEST_SUBJECT = "ms-subject-22222222"
_TEST_OID = "ms-oid-33333333"
_TEST_EMAIL = "msuser@testcorp.example.com"
_TEST_NAME = "MS Test User"
_TEST_SCOPE = "access_as_user"
_TEST_KID = "test-kid-44444444"


def _build_test_settings(
    *,
    client_id: str = _TEST_CLIENT_ID,
    tenant_id: str = _TEST_TENANT_ID,
    required_scope: str = _TEST_SCOPE,
    allowed_tenants: tuple[str, ...] = (),
) -> MicrosoftAuthSettings:
    return MicrosoftAuthSettings(
        client_id=client_id,
        tenant_id=tenant_id,
        required_scope=required_scope,
        allowed_tenants=allowed_tenants,
    )


def _build_test_token(
    *,
    sub: str = _TEST_SUBJECT,
    iss: str = _TEST_ISSUER,
    aud: str = _TEST_CLIENT_ID,
    tid: str = _TEST_TENANT_ID,
    scp: str = _TEST_SCOPE,
    email: str | None = _TEST_EMAIL,
    name: str | None = _TEST_NAME,
    oid: str | None = _TEST_OID,
    exp: int | None = None,
    nbf: int | None = None,
    private_key_pem: bytes = _TEST_RSA_PRIVATE_PEM,
    kid: str = _TEST_KID,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Build a signed test JWT using RS256."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": sub,
        "iss": iss,
        "aud": aud,
        "tid": tid,
        "scp": scp,
        "exp": exp if exp is not None else now + 3600,
        "iat": now,
    }
    if nbf is not None:
        payload["nbf"] = nbf
    if email is not None:
        payload["email"] = email
    if name is not None:
        payload["name"] = name
    if oid is not None:
        payload["oid"] = oid
    if extra_claims:
        payload.update(extra_claims)

    return pyjwt.encode(
        payload,
        private_key_pem,
        algorithm="RS256",
        headers={"kid": kid},
    )


def _mock_jwk_client(verifier: MicrosoftTokenVerifier) -> None:
    """Patch the verifier's JWK client to return the test public key."""
    mock_key = MagicMock()
    mock_key.key = _TEST_RSA_PUBLIC_KEY
    verifier._jwk_client.get_signing_key_from_jwt = MagicMock(return_value=mock_key)


def _mock_jwk_client_wrong_key(verifier: MicrosoftTokenVerifier) -> None:
    """Patch the verifier's JWK client to return the wrong public key."""
    mock_key = MagicMock()
    mock_key.key = _WRONG_RSA_KEY.public_key()
    verifier._jwk_client.get_signing_key_from_jwt = MagicMock(return_value=mock_key)


def _mock_jwk_client_not_found(verifier: MicrosoftTokenVerifier) -> None:
    """Patch the verifier's JWK client to raise kid-not-found."""
    from jwt import PyJWKClientError
    verifier._jwk_client.get_signing_key_from_jwt = MagicMock(
        side_effect=InvalidMicrosoftTokenError("Token signing key not found in Microsoft JWKS.")
    )


def _mock_jwk_client_unavailable(verifier: MicrosoftTokenVerifier) -> None:
    """Patch the verifier's JWK client to simulate JWKS unavailability."""
    verifier._jwk_client.get_signing_key_from_jwt = MagicMock(
        side_effect=MicrosoftJWKSUnavailableError()
    )


# ═══════════════════════════════════════════════════════════════════════════
# Unit Tests — MicrosoftTokenVerifier
# ═══════════════════════════════════════════════════════════════════════════


class TestMicrosoftTokenVerifier:
    """Unit tests for the MicrosoftTokenVerifier class."""

    def test_disabled_raises_error(self) -> None:
        """Microsoft auth disabled when client_id is empty."""
        settings = _build_test_settings(client_id="")
        verifier = MicrosoftTokenVerifier(settings)
        with pytest.raises(MicrosoftAuthDisabledError):
            verifier.verify("any-token")

    def test_valid_token_new_identity(self) -> None:
        """A valid token produces a VerifiedMicrosoftIdentity."""
        settings = _build_test_settings()
        verifier = MicrosoftTokenVerifier(settings)
        _mock_jwk_client(verifier)

        token = _build_test_token()
        identity = verifier.verify(token)

        assert identity.subject == _TEST_SUBJECT
        assert identity.issuer == _TEST_ISSUER
        assert identity.tenant_id == _TEST_TENANT_ID
        assert identity.object_id == _TEST_OID
        assert identity.email == _TEST_EMAIL
        assert identity.display_name == _TEST_NAME

    def test_expired_token_rejected(self) -> None:
        """An expired token is rejected."""
        settings = _build_test_settings()
        verifier = MicrosoftTokenVerifier(settings)
        _mock_jwk_client(verifier)

        token = _build_test_token(exp=int(time.time()) - 3600)
        with pytest.raises(InvalidMicrosoftTokenError, match="expired"):
            verifier.verify(token)

    def test_future_nbf_rejected(self) -> None:
        """A token with nbf in the future is rejected."""
        settings = _build_test_settings()
        verifier = MicrosoftTokenVerifier(settings)
        _mock_jwk_client(verifier)

        token = _build_test_token(nbf=int(time.time()) + 3600)
        with pytest.raises(InvalidMicrosoftTokenError, match="not yet valid"):
            verifier.verify(token)

    def test_wrong_audience_rejected(self) -> None:
        """A token with wrong audience is rejected."""
        settings = _build_test_settings()
        verifier = MicrosoftTokenVerifier(settings)
        _mock_jwk_client(verifier)

        token = _build_test_token(aud="wrong-audience")
        with pytest.raises(InvalidMicrosoftTokenError, match="audience"):
            verifier.verify(token)

    def test_graph_token_rejected(self) -> None:
        """A Microsoft Graph token is explicitly rejected."""
        settings = _build_test_settings(client_id="https://graph.microsoft.com")
        verifier = MicrosoftTokenVerifier(settings)
        _mock_jwk_client(verifier)

        token = _build_test_token(aud="https://graph.microsoft.com")
        with pytest.raises(InvalidMicrosoftTokenError, match="Graph"):
            verifier.verify(token)

    def test_wrong_issuer_rejected(self) -> None:
        """A token with a mismatched issuer is rejected."""
        settings = _build_test_settings()
        verifier = MicrosoftTokenVerifier(settings)
        _mock_jwk_client(verifier)

        token = _build_test_token(iss="https://evil.example.com/v2.0")
        with pytest.raises(InvalidMicrosoftTokenError, match="issuer"):
            verifier.verify(token)

    def test_issuer_tid_mismatch_rejected(self) -> None:
        """A token whose issuer tid doesn't match the tid claim is rejected."""
        settings = _build_test_settings()
        verifier = MicrosoftTokenVerifier(settings)
        _mock_jwk_client(verifier)

        # issuer says tenant-A but tid claim says tenant-B
        token = _build_test_token(
            iss="https://login.microsoftonline.com/other-tenant/v2.0",
            tid=_TEST_TENANT_ID,
        )
        with pytest.raises(InvalidMicrosoftTokenError, match="issuer"):
            verifier.verify(token)

    def test_missing_sub_rejected(self) -> None:
        """A token without sub claim is rejected."""
        settings = _build_test_settings()
        verifier = MicrosoftTokenVerifier(settings)
        _mock_jwk_client(verifier)

        token = _build_test_token(sub="")
        with pytest.raises(InvalidMicrosoftTokenError, match="subject"):
            verifier.verify(token)

    def test_missing_tid_rejected(self) -> None:
        """A token without tid claim is rejected during decode (required claims)."""
        settings = _build_test_settings()
        verifier = MicrosoftTokenVerifier(settings)
        _mock_jwk_client(verifier)

        # Build token without tid
        now = int(time.time())
        payload = {
            "sub": _TEST_SUBJECT,
            "iss": _TEST_ISSUER,
            "aud": _TEST_CLIENT_ID,
            "scp": _TEST_SCOPE,
            "exp": now + 3600,
            "iat": now,
        }
        token = pyjwt.encode(
            payload, _TEST_RSA_PRIVATE_PEM, algorithm="RS256",
            headers={"kid": _TEST_KID},
        )
        with pytest.raises(InvalidMicrosoftTokenError):
            verifier.verify(token)

    def test_disallowed_tenant_rejected(self) -> None:
        """A token from a disallowed tenant is rejected."""
        settings = _build_test_settings(allowed_tenants=("allowed-tenant-only",))
        verifier = MicrosoftTokenVerifier(settings)
        _mock_jwk_client(verifier)

        token = _build_test_token()  # uses _TEST_TENANT_ID which is not in the allow list
        with pytest.raises(InvalidMicrosoftTenantError):
            verifier.verify(token)

    def test_allowed_tenant_accepted(self) -> None:
        """A token from an allowed tenant is accepted."""
        settings = _build_test_settings(allowed_tenants=(_TEST_TENANT_ID,))
        verifier = MicrosoftTokenVerifier(settings)
        _mock_jwk_client(verifier)

        token = _build_test_token()
        identity = verifier.verify(token)
        assert identity.tenant_id == _TEST_TENANT_ID

    def test_missing_required_scope_rejected(self) -> None:
        """A token missing the required scope is rejected."""
        settings = _build_test_settings()
        verifier = MicrosoftTokenVerifier(settings)
        _mock_jwk_client(verifier)

        token = _build_test_token(scp="some_other_scope")
        with pytest.raises(MissingRequiredScopeError):
            verifier.verify(token)

    def test_empty_scope_rejected(self) -> None:
        """A token with empty scp claim is rejected."""
        settings = _build_test_settings()
        verifier = MicrosoftTokenVerifier(settings)
        _mock_jwk_client(verifier)

        token = _build_test_token(scp="")
        with pytest.raises(MissingRequiredScopeError):
            verifier.verify(token)

    def test_invalid_signature_rejected(self) -> None:
        """A token signed with the wrong key is rejected."""
        settings = _build_test_settings()
        verifier = MicrosoftTokenVerifier(settings)
        _mock_jwk_client(verifier)  # uses correct public key

        # Token signed with wrong private key
        token = _build_test_token(private_key_pem=_WRONG_RSA_PRIVATE_PEM)
        with pytest.raises(InvalidMicrosoftTokenError):
            verifier.verify(token)

    def test_unknown_kid_rejected(self) -> None:
        """A token with unknown kid is rejected."""
        settings = _build_test_settings()
        verifier = MicrosoftTokenVerifier(settings)
        _mock_jwk_client_not_found(verifier)

        token = _build_test_token(kid="unknown-kid")
        with pytest.raises(InvalidMicrosoftTokenError, match="signing key"):
            verifier.verify(token)

    def test_jwks_unavailable_raises(self) -> None:
        """JWKS unavailability raises the correct error."""
        settings = _build_test_settings()
        verifier = MicrosoftTokenVerifier(settings)
        _mock_jwk_client_unavailable(verifier)

        token = _build_test_token()
        with pytest.raises(MicrosoftJWKSUnavailableError):
            verifier.verify(token)

    def test_email_from_preferred_username(self) -> None:
        """When email claim is absent, preferred_username is used if it looks like email."""
        settings = _build_test_settings()
        verifier = MicrosoftTokenVerifier(settings)
        _mock_jwk_client(verifier)

        token = _build_test_token(
            email=None,
            extra_claims={"preferred_username": "fallback@test.example.com"},
        )
        identity = verifier.verify(token)
        assert identity.email == "fallback@test.example.com"

    def test_no_email_produces_none(self) -> None:
        """When no email-like claim exists, email is None."""
        settings = _build_test_settings()
        verifier = MicrosoftTokenVerifier(settings)
        _mock_jwk_client(verifier)

        token = _build_test_token(email=None)
        identity = verifier.verify(token)
        assert identity.email is None

    def test_raw_token_not_in_error_message(self) -> None:
        """Error messages must never contain the raw token."""
        settings = _build_test_settings()
        verifier = MicrosoftTokenVerifier(settings)
        _mock_jwk_client(verifier)

        token = _build_test_token(exp=int(time.time()) - 3600)
        try:
            verifier.verify(token)
            assert False, "Expected exception"
        except InvalidMicrosoftTokenError as e:
            assert token not in str(e)
            assert token not in e.detail
            assert token not in e.error_code


# ═══════════════════════════════════════════════════════════════════════════
# Integration Tests — POST /api/auth/microsoft endpoint
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_db() -> AsyncMock:
    """Fixture to generate a mock async database session."""
    db = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
def client(mock_db: AsyncMock) -> TestClient:
    """TestClient with overridden database session dependency."""
    app.dependency_overrides[get_async_session] = lambda: mock_db
    # Reset the cached verifier so tests can inject settings cleanly
    _auth_router_module._microsoft_verifier = None
    yield TestClient(app)
    app.dependency_overrides.clear()
    _auth_router_module._microsoft_verifier = None


def _make_verified_identity(
    *,
    subject: str = _TEST_SUBJECT,
    issuer: str = _TEST_ISSUER,
    tenant_id: str = _TEST_TENANT_ID,
    object_id: str | None = _TEST_OID,
    email: str | None = _TEST_EMAIL,
    display_name: str | None = _TEST_NAME,
) -> VerifiedMicrosoftIdentity:
    return VerifiedMicrosoftIdentity(
        subject=subject,
        issuer=issuer,
        tenant_id=tenant_id,
        object_id=object_id,
        email=email,
        display_name=display_name,
    )


class TestMicrosoftEndpoint:
    """Integration tests for POST /api/auth/microsoft."""

    def test_microsoft_auth_disabled(self, client: TestClient) -> None:
        """Returns 503 when AZURE_CLIENT_ID is not set."""
        with patch.dict("os.environ", {"AZURE_CLIENT_ID": ""}, clear=False):
            response = client.post(
                "/api/auth/microsoft",
                json={"access_token": "any-token"},
            )
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json()["detail"] == "microsoft_auth_disabled"

    def test_valid_token_new_user(
        self, client: TestClient, mock_db: AsyncMock,
    ) -> None:
        """A valid Microsoft token for a new user creates the user and returns a JWT."""
        identity = _make_verified_identity()

        with patch.object(
            MicrosoftTokenVerifier, "verify", return_value=identity,
        ), patch.dict("os.environ", {"AZURE_CLIENT_ID": _TEST_CLIENT_ID}, clear=False):
            # No existing external identity
            mock_execute_result_ext = MagicMock()
            mock_execute_result_ext.scalar_one_or_none.return_value = None

            # No existing user by email
            mock_execute_result_user = MagicMock()
            mock_execute_result_user.scalar_one_or_none.return_value = None

            mock_db.execute.side_effect = [
                mock_execute_result_ext,  # ExternalIdentityRepository lookup
                mock_execute_result_user,  # UserRepository.get_user_by_email
            ]

            # Mock db.refresh for create_user
            async def mock_refresh(obj):
                if isinstance(obj, UserRecord):
                    obj.id = 999
                elif isinstance(obj, ExternalIdentityRecord):
                    obj.id = 1
            mock_db.refresh = AsyncMock(side_effect=mock_refresh)

            response = client.post(
                "/api/auth/microsoft",
                json={"access_token": "valid-test-token"},
            )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"
        # The token should not contain the raw Microsoft token
        assert "valid-test-token" not in body["access_token"]

    def test_valid_token_existing_identity(
        self, client: TestClient, mock_db: AsyncMock,
    ) -> None:
        """A valid Microsoft token for a returning user issues a JWT."""
        identity = _make_verified_identity()

        existing_user = UserRecord(
            id=42,
            email=_TEST_EMAIL,
            hashed_password=None,
            full_name=_TEST_NAME,
        )
        existing_ext = ExternalIdentityRecord(
            id=1,
            user_id=42,
            provider="microsoft",
            issuer=_TEST_ISSUER,
            subject=_TEST_SUBJECT,
            tenant_id=_TEST_TENANT_ID,
        )

        with patch.object(
            MicrosoftTokenVerifier, "verify", return_value=identity,
        ), patch.dict("os.environ", {"AZURE_CLIENT_ID": _TEST_CLIENT_ID}, clear=False):
            # External identity found
            mock_result_ext = MagicMock()
            mock_result_ext.scalar_one_or_none.return_value = existing_ext

            # User found
            mock_result_user = MagicMock()
            mock_result_user.scalar_one_or_none.return_value = existing_user

            mock_db.execute.side_effect = [
                mock_result_ext,
                mock_result_user,
            ]

            response = client.post(
                "/api/auth/microsoft",
                json={"access_token": "returning-user-token"},
            )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"

    def test_invalid_token_returns_401(
        self, client: TestClient,
    ) -> None:
        """An invalid Microsoft token returns 401."""
        with patch.object(
            MicrosoftTokenVerifier, "verify",
            side_effect=InvalidMicrosoftTokenError(),
        ), patch.dict("os.environ", {"AZURE_CLIENT_ID": _TEST_CLIENT_ID}, clear=False):
            response = client.post(
                "/api/auth/microsoft",
                json={"access_token": "bad-token"},
            )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()["detail"] == "invalid_microsoft_token"

    def test_disallowed_tenant_returns_403(
        self, client: TestClient,
    ) -> None:
        """A token from a disallowed tenant returns 403."""
        with patch.object(
            MicrosoftTokenVerifier, "verify",
            side_effect=InvalidMicrosoftTenantError(),
        ), patch.dict("os.environ", {"AZURE_CLIENT_ID": _TEST_CLIENT_ID}, clear=False):
            response = client.post(
                "/api/auth/microsoft",
                json={"access_token": "wrong-tenant"},
            )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["detail"] == "invalid_microsoft_tenant"

    def test_missing_scope_returns_403(
        self, client: TestClient,
    ) -> None:
        """A token missing the required scope returns 403."""
        with patch.object(
            MicrosoftTokenVerifier, "verify",
            side_effect=MissingRequiredScopeError(),
        ), patch.dict("os.environ", {"AZURE_CLIENT_ID": _TEST_CLIENT_ID}, clear=False):
            response = client.post(
                "/api/auth/microsoft",
                json={"access_token": "no-scope"},
            )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["detail"] == "missing_required_scope"

    def test_jwks_unavailable_returns_503(
        self, client: TestClient,
    ) -> None:
        """JWKS unavailability returns 503."""
        with patch.object(
            MicrosoftTokenVerifier, "verify",
            side_effect=MicrosoftJWKSUnavailableError(),
        ), patch.dict("os.environ", {"AZURE_CLIENT_ID": _TEST_CLIENT_ID}, clear=False):
            response = client.post(
                "/api/auth/microsoft",
                json={"access_token": "jwks-down"},
            )
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json()["detail"] == "microsoft_jwks_unavailable"

    def test_matching_email_returns_conflict(
        self, client: TestClient, mock_db: AsyncMock,
    ) -> None:
        """A new MS identity whose email matches an existing local user returns 409."""
        identity = _make_verified_identity(email="existing@company.com")

        existing_local_user = UserRecord(
            id=10,
            email="existing@company.com",
            hashed_password="$2b$12$hashedpassword",
            full_name="Local User",
        )

        with patch.object(
            MicrosoftTokenVerifier, "verify", return_value=identity,
        ), patch.dict("os.environ", {"AZURE_CLIENT_ID": _TEST_CLIENT_ID}, clear=False):
            # No external identity found
            mock_result_ext = MagicMock()
            mock_result_ext.scalar_one_or_none.return_value = None

            # Existing user found by email
            mock_result_user = MagicMock()
            mock_result_user.scalar_one_or_none.return_value = existing_local_user

            mock_db.execute.side_effect = [
                mock_result_ext,
                mock_result_user,
            ]

            response = client.post(
                "/api/auth/microsoft",
                json={"access_token": "conflict-token"},
            )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["detail"] == "account_linking_required"

    def test_no_email_returns_onboarding_required(
        self, client: TestClient, mock_db: AsyncMock,
    ) -> None:
        """A new MS identity with no email returns 422."""
        identity = _make_verified_identity(email=None)

        with patch.object(
            MicrosoftTokenVerifier, "verify", return_value=identity,
        ), patch.dict("os.environ", {"AZURE_CLIENT_ID": _TEST_CLIENT_ID}, clear=False):
            # No external identity found
            mock_result_ext = MagicMock()
            mock_result_ext.scalar_one_or_none.return_value = None

            mock_db.execute.side_effect = [mock_result_ext]

            response = client.post(
                "/api/auth/microsoft",
                json={"access_token": "no-email-token"},
            )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.json()["detail"] == "microsoft_onboarding_required"

    def test_orphaned_identity_returns_conflict(
        self, client: TestClient, mock_db: AsyncMock,
    ) -> None:
        """An external identity whose user was deleted returns 409."""
        identity = _make_verified_identity()

        orphaned_ext = ExternalIdentityRecord(
            id=99,
            user_id=999,
            provider="microsoft",
            issuer=_TEST_ISSUER,
            subject=_TEST_SUBJECT,
        )

        with patch.object(
            MicrosoftTokenVerifier, "verify", return_value=identity,
        ), patch.dict("os.environ", {"AZURE_CLIENT_ID": _TEST_CLIENT_ID}, clear=False):
            # External identity found
            mock_result_ext = MagicMock()
            mock_result_ext.scalar_one_or_none.return_value = orphaned_ext

            # User NOT found (orphan)
            mock_result_user = MagicMock()
            mock_result_user.scalar_one_or_none.return_value = None

            mock_db.execute.side_effect = [
                mock_result_ext,
                mock_result_user,
            ]

            response = client.post(
                "/api/auth/microsoft",
                json={"access_token": "orphan-token"},
            )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["detail"] == "microsoft_identity_conflict"

    def test_raw_token_not_in_error_response(
        self, client: TestClient,
    ) -> None:
        """Raw Microsoft tokens must never appear in error responses."""
        raw_token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsImtpZCI6InRlc3Qta2lkIn0.fakepayload.fakesig"

        with patch.object(
            MicrosoftTokenVerifier, "verify",
            side_effect=InvalidMicrosoftTokenError(),
        ), patch.dict("os.environ", {"AZURE_CLIENT_ID": _TEST_CLIENT_ID}, clear=False):
            response = client.post(
                "/api/auth/microsoft",
                json={"access_token": raw_token},
            )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        response_text = response.text
        assert raw_token not in response_text

    def test_graph_token_audience_rejected_at_endpoint(
        self, client: TestClient,
    ) -> None:
        """A Microsoft Graph token is rejected via the verifier."""
        with patch.object(
            MicrosoftTokenVerifier, "verify",
            side_effect=InvalidMicrosoftTokenError(
                "Microsoft Graph tokens cannot be used for LogSentinel API authentication."
            ),
        ), patch.dict("os.environ", {"AZURE_CLIENT_ID": _TEST_CLIENT_ID}, clear=False):
            response = client.post(
                "/api/auth/microsoft",
                json={"access_token": "graph-token"},
            )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ═══════════════════════════════════════════════════════════════════════════
# Regression Tests — Existing Auth
# ═══════════════════════════════════════════════════════════════════════════


class TestExistingAuthUnchanged:
    """Verify that existing authentication endpoints are not broken."""

    @pytest.fixture
    def mock_db(self) -> AsyncMock:
        db = AsyncMock()
        db.add = MagicMock()
        return db

    @pytest.fixture
    def client(self, mock_db: AsyncMock) -> TestClient:
        app.dependency_overrides[get_async_session] = lambda: mock_db
        yield TestClient(app)
        app.dependency_overrides.clear()

    def test_email_login_still_works(
        self, client: TestClient, mock_db: AsyncMock,
    ) -> None:
        """Email/password login continues to function."""
        from backend.app.security.auth import hash_password

        hashed_pw = hash_password("validpassword")
        user = UserRecord(
            id=1,
            email="user@company.com",
            hashed_password=hashed_pw,
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = mock_result

        response = client.post(
            "/api/auth/login",
            json={"email": "user@company.com", "password": "validpassword"},
        )
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"

    def test_registration_still_works(
        self, client: TestClient, mock_db: AsyncMock,
    ) -> None:
        """User registration continues to function."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        async def mock_refresh(u):
            u.id = 100
        mock_db.refresh = AsyncMock(side_effect=mock_refresh)

        response = client.post(
            "/api/auth/register",
            json={
                "email": "new@company.com",
                "password": "strongpassword123",
                "fullName": "New User",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body["email"] == "new@company.com"

    def test_me_endpoint_still_works(
        self, client: TestClient, mock_db: AsyncMock,
    ) -> None:
        """GET /api/auth/me continues to function with valid JWT."""
        user = UserRecord(
            id=42,
            email="profile@company.com",
            hashed_password="hash",
            full_name="Profile User",
            organization="Test Org",
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = mock_result

        token = create_access_token({"sub": "profile@company.com"})
        response = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["email"] == "profile@company.com"
