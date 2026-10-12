"""Unit tests for user database schema, hashing utilities, JWT validation, and FastAPI auth routes."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from backend.app.core.database import get_async_session
from backend.app.core.orm import (
    AuthSessionRecord,
    TenantMembershipRecord,
    TenantRecord,
    UserRecord,
)
from backend.app.main import _get_frontend_origins, app
from backend.app.security.auth import (
    JWT_SECRET_KEY,
    JWT_ALGORITHM,
    hash_password,
    verify_password,
    create_access_token,
)

# ─── Password Hashing & Verification tests ────────────────────────────────────

def test_password_hashing_and_verification() -> None:
    password = "supersecretpassword123"
    hashed = hash_password(password)
    
    assert hashed != password
    assert hashed.startswith("$argon2id$")  # Argon2id PHC prefix
    
    # Valid validation
    assert verify_password(password, hashed) is True
    
    # Invalid validation
    assert verify_password("wrongpassword", hashed) is False
    assert verify_password("", hashed) is False


# ─── JWT Generation & Expiry tests ──────────────────────────────────────────

def test_jwt_generation_and_decoding() -> None:
    data = {"sub": "user@company.com"}
    token = create_access_token(data)
    
    # Decode token
    payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM], issuer="logsentinel", audience="logsentinel-api")
    assert payload["sub"] == "user@company.com"
    assert "exp" in payload


# ─── API Endpoint Mock Tests ──────────────────────────────────────────────────

@pytest.fixture
def mock_db() -> AsyncMock:
    """Fixture to generate a mock async database session."""
    db = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
def client(mock_db: AsyncMock) -> TestClient:
    """Fixture returning a TestClient with overridden database session dependency."""
    app.dependency_overrides[get_async_session] = lambda: mock_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_register_user_success(client: TestClient, mock_db: AsyncMock) -> None:
    # Set up DB query mock: first lookup by email finds nothing
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_execute_result

    # Mock db.refresh to assign an id and behave as AsyncMock
    async def mock_refresh(u):
        u.id = 999
    mock_db.refresh = AsyncMock(side_effect=mock_refresh)

    payload = {
        "email": "newuser@company.com",
        "password": "strongpassword123",
        "fullName": "New User",
        "organization": "Acme Inc",
    }

    # Make post request
    response = client.post("/api/auth/register", json=payload)
    
    assert response.status_code == status.HTTP_201_CREATED
    body = response.json()
    assert body["email"] == "newuser@company.com"
    assert body["status"] == "pending_verification"
    assert "verification" in body["message"].lower()

    # Verify db interaction
    # Registration now persists explicit tenant membership and durable email
    # work in addition to the user row.
    added_types = {type(call.args[0]).__name__ for call in mock_db.add.call_args_list}
    assert {"TenantRecord", "UserRecord", "TenantMembershipRecord", "EmailOutboxRecord"} <= added_types
    mock_db.commit.assert_called_once()
    mock_db.flush.assert_awaited()


def test_register_user_already_exists(client: TestClient, mock_db: AsyncMock) -> None:
    # Set up DB query mock: lookup finds an existing user
    existing_user = UserRecord(
        id=1,
        email="existing@company.com",
        hashed_password="somehash",
    )
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = existing_user
    mock_db.execute.return_value = mock_execute_result

    payload = {
        "email": "existing@company.com",
        "password": "strongpassword123",
    }

    response = client.post("/api/auth/register", json=payload)
    
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == "A user with this email address already exists"


def test_login_user_success(client: TestClient, mock_db: AsyncMock) -> None:
    hashed_pw = hash_password("validpassword")
    user = UserRecord(
        id=1,
        email="user@company.com",
        hashed_password=hashed_pw,
    )
    
    # Mock find user by email
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = user
    mock_db.execute.return_value = mock_execute_result

    payload = {
        "email": "user@company.com",
        "password": "validpassword",
    }

    response = client.post("/api/auth/login", json=payload)
    
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"


def test_login_user_invalid_credentials(client: TestClient, mock_db: AsyncMock) -> None:
    hashed_pw = hash_password("validpassword")
    user = UserRecord(
        id=1,
        email="user@company.com",
        hashed_password=hashed_pw,
    )
    
    # Mock lookup
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = user
    mock_db.execute.return_value = mock_execute_result

    # Try wrong password
    response = client.post(
        "/api/auth/login",
        json={"email": "user@company.com", "password": "wrongpassword"}
    )
    
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"] == "Invalid email or password"


def _configure_profile_context(
    mock_db: AsyncMock,
    user: UserRecord | None,
    *,
    tenant: TenantRecord | None = None,
    membership: TenantMembershipRecord | None = None,
    session: AuthSessionRecord | None = None,
) -> None:
    """Provision the same user/tenant/membership authority used by production."""
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    session_result = MagicMock()
    session_result.scalar_one_or_none.return_value = session

    async def execute(statement):
        entity = statement.column_descriptions[0].get("entity")
        return session_result if entity is AuthSessionRecord else user_result

    async def scalar(statement):
        entity = statement.column_descriptions[0].get("entity")
        if user is None:
            return None
        if entity is TenantRecord:
            return tenant if tenant is not None and tenant.id == user.tenant_id else None
        if entity is TenantMembershipRecord:
            return (
                membership
                if membership is not None
                and membership.tenant_id == user.tenant_id
                and membership.user_id == user.id
                else None
            )
        return None

    mock_db.execute = AsyncMock(side_effect=execute)
    mock_db.scalar = AsyncMock(side_effect=scalar)


def _profile_user(**changes) -> UserRecord:
    values = {
        "id": 42,
        "email": "profile@company.com",
        "hashed_password": "hash",
        "full_name": "Profile User",
        "organization": "Test Org",
        "tenant_id": "tenant-a",
        "status": "active",
        "role": "viewer",
    }
    values.update(changes)
    return UserRecord(**values)


def _active_profile_tenant(status_value: str = "active") -> TenantRecord:
    return TenantRecord(id="tenant-a", name="Tenant A", status=status_value)


def _active_profile_membership(status_value: str = "active") -> TenantMembershipRecord:
    return TenantMembershipRecord(
        tenant_id="tenant-a", user_id=42, role="viewer", status=status_value
    )


def test_get_profile_success(client: TestClient, mock_db: AsyncMock) -> None:
    user = _profile_user()
    _configure_profile_context(
        mock_db,
        user,
        tenant=_active_profile_tenant(),
        membership=_active_profile_membership(),
    )

    token = create_access_token({"sub": user.email})
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/api/auth/me", headers=headers)

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["id"] == 42
    assert body["email"] == "profile@company.com"
    assert body["full_name"] == "Profile User"
    assert body["organization"] == "Test Org"


def test_get_profile_missing_user_fails_closed(client: TestClient, mock_db: AsyncMock) -> None:
    _configure_profile_context(mock_db, None)
    token = create_access_token({"sub": "missing@company.com"})

    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_get_profile_inactive_user_fails_closed(client: TestClient, mock_db: AsyncMock) -> None:
    user = _profile_user(status="suspended")
    _configure_profile_context(
        mock_db,
        user,
        tenant=_active_profile_tenant(),
        membership=_active_profile_membership(),
    )
    token = create_access_token({"sub": user.email})

    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.parametrize(
    "tenant,membership",
    [
        (_active_profile_tenant(), None),
        (_active_profile_tenant(), _active_profile_membership("suspended")),
        (_active_profile_tenant("suspended"), _active_profile_membership()),
        (TenantRecord(id="tenant-b", name="Tenant B", status="active"), _active_profile_membership()),
    ],
    ids=["missing-membership", "inactive-membership", "inactive-tenant", "wrong-tenant"],
)
def test_get_profile_inactive_or_wrong_tenant_context_fails_closed(
    client: TestClient,
    mock_db: AsyncMock,
    tenant: TenantRecord,
    membership: TenantMembershipRecord | None,
) -> None:
    user = UserRecord(
        id=42,
        email="profile@company.com",
        hashed_password="hash",
        full_name="Profile User",
        organization="Test Org",
        tenant_id="tenant-a",
        status="active",
        role="viewer",
    )
    _configure_profile_context(
        mock_db, user, tenant=tenant, membership=membership
    )
    token = create_access_token({"sub": user.email})
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/api/auth/me", headers=headers)

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_get_profile_invalid_session_fails_closed(client: TestClient, mock_db: AsyncMock) -> None:
    user = _profile_user()
    _configure_profile_context(
        mock_db,
        user,
        tenant=_active_profile_tenant(),
        membership=_active_profile_membership(),
        session=None,
    )
    token = create_access_token({"sub": user.email}, session_id="missing-session")

    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_get_profile_unauthorized_missing_token(client: TestClient) -> None:
    response = client.get("/api/auth/me")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_get_profile_unauthorized_invalid_token(client: TestClient) -> None:
    headers = {"Authorization": "Bearer invalidtokenhere"}
    response = client.get("/api/auth/me", headers=headers)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_auth_cors_is_limited_to_the_configured_frontend(
    client: TestClient,
) -> None:
    assert _get_frontend_origins("http://localhost:5173/") == ["http://localhost:5173"]
    with pytest.raises(ValueError, match=r"invalid origin"):
        _get_frontend_origins("*")
    with pytest.raises(ValueError, match=r"invalid origin"):
        _get_frontend_origins("https://example.com/login")

    cors_middleware = next(
        middleware
        for middleware in app.user_middleware
        if middleware.cls is CORSMiddleware
    )
    allowed_origins = cors_middleware.kwargs["allow_origins"]
    assert len(allowed_origins) >= 1
    assert "*" not in allowed_origins

    allowed_response = client.options(
        "/api/auth/login",
        headers={
            "Origin": allowed_origins[0],
            "Access-Control-Request-Method": "POST",
        },
    )
    assert allowed_response.status_code == status.HTTP_200_OK
    assert allowed_response.headers["access-control-allow-origin"] == allowed_origins[0]

    denied_response = client.options(
        "/api/auth/login",
        headers={
            "Origin": "https://attacker.invalid",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in denied_response.headers


def test_forgot_password_never_generates_or_logs_a_reset_token(
    client: TestClient,
    mock_db: AsyncMock,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    user = UserRecord(
        id=42,
        email="reset-user@company.com",
        hashed_password="hash",
    )
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = user
    mock_db.execute.return_value = mock_execute_result
    sensitive_marker = "sensitive-reset-token-must-not-appear"

    with patch(
        "backend.app.routers.auth_router.create_access_token",
        return_value=sensitive_marker,
    ) as create_token:
        response = client.post(
            "/api/auth/forgot-password",
            json={"email": user.email},
        )

    assert response.status_code == status.HTTP_200_OK
    assert create_token.call_count == 0
    assert sensitive_marker not in response.text
    assert sensitive_marker not in caplog.text
    captured = capsys.readouterr()
    assert sensitive_marker not in captured.out
    assert sensitive_marker not in captured.err
