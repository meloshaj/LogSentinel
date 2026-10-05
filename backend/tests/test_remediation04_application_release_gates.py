from __future__ import annotations

import inspect
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import HTTPException, Request, Response

from backend.app.core.orm import EmailOutboxRecord, RefreshTokenRecord
from backend.app.routers import auth_router
from backend.app.security.auth import (
    JWT_ALGORITHM,
    JWT_AUDIENCE,
    JWT_ISSUER,
    JWT_SECRET_KEY,
    authenticate_token,
    create_access_token,
)
from backend.app.services.email_outbox import enqueue_email
from backend.app.services.sessions import create_session, rotate_refresh_token


def test_access_jwt_has_required_claims_and_short_lifetime() -> None:
    token = create_access_token({"sub": "user@example.test"}, session_id="session-1")
    payload = jwt.decode(
        token,
        JWT_SECRET_KEY,
        algorithms=[JWT_ALGORITHM],
        issuer=JWT_ISSUER,
        audience=JWT_AUDIENCE,
    )
    assert {"sub", "exp", "iat", "iss", "aud", "jti", "sid"} <= payload.keys()
    assert payload["exp"] - payload["iat"] <= 15 * 60


def test_session_cookie_scope_keeps_refresh_http_only_and_csrf_readable() -> None:
    request = Request(
        {
            "type": "http",
            "scheme": "https",
            "server": ("example.test", 443),
            "path": "/",
            "headers": [],
        }
    )
    response = Response()
    issued = SimpleNamespace(
        refresh_token="refresh-secret",
        csrf_token="csrf-secret",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    auth_router._set_session_cookies(response, request, issued)
    cookies = response.headers.getlist("set-cookie")
    refresh = next(
        value for value in cookies if value.startswith("logsentinel_refresh=")
    )
    csrf = next(value for value in cookies if value.startswith("logsentinel_csrf="))
    assert "HttpOnly" in refresh and "Secure" in refresh and "Path=/api/auth" in refresh
    assert "HttpOnly" not in csrf and "Secure" in csrf and "Path=/;" in csrf


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "issuer,audience,expired",
    [
        ("wrong", JWT_AUDIENCE, False),
        (JWT_ISSUER, "wrong", False),
        (JWT_ISSUER, JWT_AUDIENCE, True),
    ],
)
async def test_access_jwt_rejects_wrong_authority_or_expiry(
    issuer: str, audience: str, expired: bool
) -> None:
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "u@example.test",
            "iat": now - timedelta(minutes=2),
            "exp": now - timedelta(seconds=1)
            if expired
            else now + timedelta(minutes=2),
            "iss": issuer,
            "aud": audience,
            "jti": "j",
        },
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await authenticate_token(token, db)
    assert exc.value.status_code == 401
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_creation_stores_only_refresh_hash() -> None:
    db = AsyncMock()
    db.add = MagicMock()
    user = SimpleNamespace(id=7, tenant_id="tenant-a")
    issued = await create_session(db, user)
    refresh = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], RefreshTokenRecord)
    )
    assert refresh.token_hash != issued.refresh_token
    assert issued.refresh_token not in repr(refresh.__dict__)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_rotated_refresh_token_reuse_revokes_family() -> None:
    now = datetime.now(timezone.utc)
    token = SimpleNamespace(
        token_hash="hash",
        consumed_at=now,
        revoked_at=None,
        expires_at=now + timedelta(days=1),
    )
    session = SimpleNamespace(
        id="s",
        user_id=1,
        csrf_hash="unused",
        revoked_at=None,
        expires_at=now + timedelta(days=1),
        reuse_detected_at=None,
    )
    user = SimpleNamespace(id=1, status="active")
    result = MagicMock()
    result.first.return_value = (token, session, user)
    db = AsyncMock()
    db.execute.return_value = result
    assert await rotate_refresh_token(db, "old", "csrf") is None
    assert session.revoked_at is not None and session.reuse_detected_at is not None


@pytest.mark.asyncio
async def test_refresh_success_rotates_the_token() -> None:
    now = datetime.now(timezone.utc)
    raw, csrf = "refresh-secret", "csrf-secret"
    token = SimpleNamespace(
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        consumed_at=None,
        revoked_at=None,
        expires_at=now + timedelta(days=1),
        last_used_at=None,
        rotated_to=None,
    )
    session = SimpleNamespace(
        id="s",
        user_id=1,
        csrf_hash=hashlib.sha256(csrf.encode()).hexdigest(),
        revoked_at=None,
        expires_at=now + timedelta(days=1),
        reuse_detected_at=None,
        last_used_at=None,
    )
    user = SimpleNamespace(id=1, status="active")
    result = MagicMock()
    result.first.return_value = (token, session, user)
    db = AsyncMock()
    db.execute.return_value = result
    db.add = MagicMock()
    rotated = await rotate_refresh_token(db, raw, csrf)
    assert rotated is not None
    assert rotated[1].refresh_token != raw
    assert token.consumed_at is not None and token.rotated_to is not None


@pytest.mark.asyncio
async def test_suspended_user_cannot_refresh_and_session_is_revoked() -> None:
    now = datetime.now(timezone.utc)
    raw, csrf = "refresh-secret", "csrf-secret"
    token = SimpleNamespace(
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        consumed_at=None,
        revoked_at=None,
        expires_at=now + timedelta(days=1),
    )
    session = SimpleNamespace(
        id="s",
        csrf_hash=hashlib.sha256(csrf.encode()).hexdigest(),
        revoked_at=None,
        expires_at=now + timedelta(days=1),
    )
    user = SimpleNamespace(id=1, status="suspended")
    result = MagicMock()
    result.first.return_value = (token, session, user)
    db = AsyncMock()
    db.execute.return_value = result
    assert await rotate_refresh_token(db, raw, csrf) is None
    assert session.revoked_at is not None


@pytest.mark.asyncio
async def test_revoked_access_session_is_rejected() -> None:
    user = SimpleNamespace(
        id=1, email="u@example.test", status="active", password_changed_at=None
    )
    session = SimpleNamespace(
        user_id=1,
        revoked_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    first = MagicMock()
    first.scalar_one_or_none.return_value = user
    second = MagicMock()
    second.scalar_one_or_none.return_value = session
    db = AsyncMock()
    db.execute.side_effect = [first, second]
    token = create_access_token({"sub": user.email}, session_id="s")
    with pytest.raises(HTTPException) as exc:
        await authenticate_token(token, db)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_email_acceptance_requires_durable_outbox_commit() -> None:
    db = AsyncMock()
    db.add = MagicMock()
    row = await enqueue_email(
        db,
        kind="verification",
        recipient="u@example.test",
        secret="123456",
        user_id=1,
        tenant_id="tenant-a",
        idempotency_key="verify:1:hash",
    )
    assert isinstance(row, EmailOutboxRecord)
    assert row.status == "pending" and row.idempotency_key == "verify:1:hash"
    assert row.recipient != "" and row.template_data != ""
    db.commit.assert_awaited_once()


def test_sso_transport_limits_and_redaction_contract() -> None:
    source = inspect.getsource(auth_router)
    assert "httpx.Timeout" in source and "follow_redirects=False" in source
    assert (
        "tokeninfo?access_token=" not in source and "tokeninfo?id_token=" not in source
    )
    assert "max_length=16384" in source


def test_application_release_migration_and_supported_topology_are_wired() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "scripts/migrations/20260912_0007_application_release_gates.sql"
    ).read_text(encoding="utf-8")
    assert all(
        name in migration
        for name in (
            "email_outbox",
            "incident_triage_history",
            "tenant_settings",
            "reuse_detected_at",
        )
    )
    worker = (root / "backend/app/cli/worker.py").read_text(encoding="utf-8")
    assert (
        "application.email_delivery_worker" in worker and 'role == "webhook"' in worker
    )
    login_readme = (root / "LOGIN/README.md").read_text(encoding="utf-8")
    assert "Archived Login UI Prototype" in login_readme


def test_model_distribution_uses_durable_pointer_and_artifact_abstraction() -> None:
    root = Path(__file__).resolve().parents[2]
    registry = (root / "backend/app/ml/model_registry.py").read_text(encoding="utf-8")
    feature = (root / "backend/app/workers/feature_worker.py").read_text(
        encoding="utf-8"
    )
    assert "ModelArtifactStore" in registry and "S3ModelArtifactStore" in registry
    assert "FROM model_artifacts" in registry and "load_registered_detector" in feature
    assert "retaining known-good model" in feature
