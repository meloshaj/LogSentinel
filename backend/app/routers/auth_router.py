"""FastAPI router for user authentication endpoints.

Provides routes for user registration with email verification,
token generation (login), user profile retrieval, Google SSO,
Microsoft SSO, GitHub SSO, email verification, password reset,
and resend verification.

Security controls:
    * Timing-attack normalization on login and forgot-password.
    * Atomic single-use verification codes via Valkey Lua scripts.
    * PostgreSQL-authoritative, idempotent single-use password-reset state.
    * Session invalidation via password_changed_at + JWT iat checks.
    * Rate limiting via SlowAPI on all sensitive endpoints.
    * Abuse prevention via per-email cooldowns and sliding-window limits.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
import urllib.parse
from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import RedirectResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    ValidationError,
    model_validator,
)
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from ..core.database import AsyncSessionDep
from ..core.email_identity import canonicalize_email
from ..core.orm import IngestionApiKeyRecord, UserRecord
from ..core.settings import (
    get_email_verification_settings,
    get_github_auth_settings,
    get_microsoft_auth_settings,
    get_password_reset_settings,
)
from ..core.rate_limit import limiter
from ..core.user_status import ACTIVE, PENDING_VERIFICATION, SUSPENDED
from ..repositories.account_repository import AccountRepository
from ..repositories.external_identity_repository import ExternalIdentityRepository
from ..repositories.user_repository import UserRepository
from ..security.auth import create_access_token
from ..security.redaction import sanitize_error_text
from ..security.tenant_context import (
    TenantContext,
    get_tenant_context,
    require_permission,
)
from ..security.tenants import resolve_provider_tenant_mapping
from ..security.microsoft_auth import (
    InvalidMicrosoftTenantError,
    InvalidMicrosoftTokenError,
    MicrosoftAuthDisabledError,
    MicrosoftAuthError,
    MicrosoftJWKSUnavailableError,
    MicrosoftTokenVerifier,
    MissingRequiredScopeError,
)
from ..services.auth_cache import AuthCacheManager
from ..services.email import send_verification_email as _send_verification_email
from ..services.email_outbox import enqueue_email
from ..services.sessions import (
    CSRF_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    create_session,
    revoke_session,
    rotate_refresh_token,
)
from ..services.password import (
    bounded_hash_password,
    bounded_verify_password,
    bounded_verify_timing_sentinel,
    generate_reset_token,
    generate_verification_code,
    hash_verification_code,
)
from ..services.password_reset import (
    PasswordResetInvalidError,
    PasswordResetUnavailableError,
    complete_password_reset,
    digest_reset_token,
    issue_password_reset,
)

logger = logging.getLogger("logsentinel.auth_router")

# Kept as a module attribute for legacy route-test fixtures.  Production email
# delivery is durable and goes through ``enqueue_email`` below.
send_verification_email = _send_verification_email

# Google OAuth configuration
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")

router = APIRouter(prefix="/api/auth", tags=["authentication"])

# ---------------------------------------------------------------------------
# Lazy Valkey cache manager — initialised on first use
# ---------------------------------------------------------------------------
_auth_cache: AuthCacheManager | None = None


def _get_auth_cache() -> AuthCacheManager:
    """Return a cached AuthCacheManager backed by the application's Redis pool."""
    global _auth_cache
    if _auth_cache is None:
        from redis.asyncio import Redis

        from ..core.redis import _redis_pool

        if _redis_pool is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Cache service is not available",
            )
        _auth_cache = AuthCacheManager(Redis(connection_pool=_redis_pool))
    return _auth_cache


def _ensure_user_can_authenticate(user: UserRecord) -> None:
    """Reject lifecycle states that must not receive a new session token."""
    if user.status is not None and user.status != ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is unavailable.",
        )


# ─── Request/Response Schemas ────────────────────────────────────────────────


class UserRegisterRequest(BaseModel):
    """Schema for user registration request."""

    email: EmailStr
    password: str = Field(
        ...,
        min_length=8,
        max_length=1024,
        description="Password must be at least 8 characters long",
    )
    fullName: str | None = Field(
        None, max_length=255, description="Optional full name of the user"
    )
    organization: str | None = Field(
        None, max_length=255, description="Optional organization name"
    )

    model_config = ConfigDict(populate_by_name=True)


class UserLoginRequest(BaseModel):
    """Schema for user login request."""

    email: EmailStr
    password: str = Field(..., min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    """Schema for login response containing the JWT token."""

    access_token: str
    token_type: str = "bearer"


def _secure_cookie(request: Request) -> bool:
    return (
        request.url.scheme == "https"
        or os.getenv("ENVIRONMENT", "").lower() == "production"
    )


def _set_session_cookies(response: Response, request: Request, issued) -> None:
    max_age = max(
        0, int((issued.expires_at - datetime.now(timezone.utc)).total_seconds())
    )
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        issued.refresh_token,
        httponly=True,
        secure=_secure_cookie(request),
        samesite="lax",
        path="/api/auth",
        max_age=max_age,
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        issued.csrf_token,
        httponly=False,
        secure=_secure_cookie(request),
        samesite="lax",
        path="/",
        max_age=max_age,
    )


async def _issue_session(
    user: UserRecord, db, request: Request, response: Response
) -> TokenResponse:
    issued = await create_session(db, user)
    _set_session_cookies(response, request, issued)
    return TokenResponse(
        access_token=create_access_token(
            {"sub": user.email, "full_name": user.full_name or ""},
            session_id=issued.session_id,
        )
    )


def _validate_cookie_request(request: Request) -> str:
    origin = request.headers.get("origin")
    allowed = {
        value.strip().rstrip("/")
        for value in os.getenv("FRONTEND_URL", "http://localhost:8080").split(",")
    }
    if origin and origin.rstrip("/") not in allowed:
        raise HTTPException(status_code=403, detail="invalid_request_origin")
    csrf = request.headers.get("x-csrf-token", "")
    cookie_csrf = request.cookies.get(CSRF_COOKIE_NAME, "")
    if not csrf or not cookie_csrf or not secrets.compare_digest(csrf, cookie_csrf):
        raise HTTPException(status_code=403, detail="invalid_csrf_token")
    return csrf


class UserResponse(BaseModel):
    """Schema for authenticated user profile details."""

    id: int
    email: str
    full_name: str | None
    organization: str | None

    model_config = ConfigDict(from_attributes=True)


class GoogleLoginRequest(BaseModel):
    """Schema for Google SSO login — accepts the id_token from the frontend."""

    credential: str = Field(..., min_length=1, max_length=16384)


class ForgotPasswordRequest(BaseModel):
    """Schema for requesting a password reset email."""

    email: EmailStr


class MicrosoftLoginRequest(BaseModel):
    """Schema for Microsoft SSO login — accepts a Microsoft access token.

    The frontend obtains a Microsoft access token via MSAL using
    Authorization Code Flow with PKCE, then sends it here for
    verification and internal JWT issuance.
    """

    access_token: str = Field(
        ...,
        min_length=1,
        max_length=16384,
        description="Microsoft access token issued for the LogSentinel API audience",
    )


class ResetPasswordRequest(BaseModel):
    """Schema for resetting the password with a valid token."""

    token: str = Field(..., min_length=32, max_length=512)
    new_password: str = Field(
        ..., min_length=8, description="New password must be at least 8 characters long"
    )
    confirm_password: str = Field(
        ..., min_length=8, description="Must match new_password"
    )

    @model_validator(mode="after")
    def passwords_match(self) -> ResetPasswordRequest:
        if self.new_password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


class VerifyEmailRequest(BaseModel):
    """Schema for email verification code submission."""

    email: EmailStr
    code: str = Field(
        ...,
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="6-digit verification code",
    )


class ResendVerificationRequest(BaseModel):
    """Schema for requesting a new verification code."""

    email: EmailStr


class RegisterResponse(BaseModel):
    """Schema for registration response."""

    message: str
    email: str
    status: str


# ─── Endpoint Route Handlers ─────────────────────────────────────────────────


@router.post(
    "/register", status_code=status.HTTP_201_CREATED, response_model=RegisterResponse
)
@limiter.limit("3/minute")
async def register_user(
    request: Request,
    payload: UserRegisterRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSessionDep,
) -> RegisterResponse:
    """Register a new user account with email verification.

    Creates the user in ``pending_verification`` status, generates a
    6-digit verification code stored in Valkey, and dispatches a
    verification email asynchronously.
    """
    normalized_email = canonicalize_email(payload.email)

    # Use row-level lock to prevent TOCTOU race condition
    existing_user = await UserRepository.get_user_by_email_for_update(
        db, normalized_email
    )

    settings = get_email_verification_settings()
    cache = _get_auth_cache()

    if existing_user is not None:
        if existing_user.status == ACTIVE:
            # Check if they have OAuth identities for a more helpful error
            identities = await ExternalIdentityRepository.get_all_by_user_id(
                db, existing_user.id
            )
            if identities:
                provider = identities[0].provider.capitalize()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"This email is already registered using {provider} Login. Please sign in with {provider}.",
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this email address already exists",
            )
        elif existing_user.status == PENDING_VERIFICATION:
            # Atomic check-and-set cooldown to prevent registration griefing
            reserved = await cache.reserve_resend_cooldown(
                normalized_email, settings.resend_cooldown_seconds
            )
            if not reserved:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="A verification code was recently dispatched for this email. Please check your inbox or wait before retrying.",
                )

            # Re-registration of a pending user: update credentials and resend code
            from sqlalchemy.sql import func

            existing_user.hashed_password = await bounded_hash_password(
                payload.password
            )
            existing_user.full_name = payload.fullName or existing_user.full_name
            existing_user.organization = (
                payload.organization or existing_user.organization
            )
            existing_user.created_at = func.now()
            await db.commit()
            await db.refresh(existing_user)
            user = existing_user
            is_new_registration = False
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this email address already exists",
            )
    else:
        # Atomic check-and-set cooldown for new user
        await cache.reserve_resend_cooldown(
            normalized_email, settings.resend_cooldown_seconds
        )

        # New user
        hashed = await bounded_hash_password(payload.password)
        try:
            user = await UserRepository.create_user(
                db=db,
                email=normalized_email,
                hashed_password=hashed,
                full_name=payload.fullName,
                organization=payload.organization,
                status=PENDING_VERIFICATION,
                commit=False,
            )
        except IntegrityError:
            # The unique database constraint is the final concurrency gate
            # when two requests observe no row at the same time.
            await db.rollback()
            await cache.delete_cooldown(normalized_email)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this email address already exists",
            )
        is_new_registration = True

    # Generate and store verification code
    settings = get_email_verification_settings()
    cache = _get_auth_cache()
    code = generate_verification_code()
    code_hash = hash_verification_code(code)

    try:
        await cache.store_verification_code(
            email=normalized_email,
            code_hash=code_hash,
            user_id=user.id,
            ttl_seconds=settings.code_ttl_seconds,
        )
        # Record rate-limit event (cooldown is already reserved)
        await cache.record_email_send(normalized_email)
    except Exception as exc:
        if is_new_registration:
            # Delete only newly created pending rows
            await db.delete(user)
            await db.commit()
        else:
            # For preemption, rollback the uncommitted transaction or re-fetch and do NOT delete the pre-existing row
            await db.rollback()
        # Clean up the reserved cooldown key on failure
        await cache.delete_cooldown(normalized_email)
        from ..services.auth_cache import AuthCacheUnavailableError

        raise AuthCacheUnavailableError(
            "Failed to persist verification code in cache."
        ) from exc

    # Dispatch verification email
    await enqueue_email(
        db,
        kind="verification",
        recipient=normalized_email,
        secret=code,
        user_id=user.id,
        tenant_id=user.tenant_id,
        idempotency_key=f"verification:{user.id}:{code_hash}",
    )

    return RegisterResponse(
        message="Verification code dispatched",
        email=normalized_email,
        status=PENDING_VERIFICATION,
    )


@router.post("/verify-email", response_model=TokenResponse)
@limiter.limit("10/minute")
async def verify_email(
    request: Request,
    payload: VerifyEmailRequest,
    response: Response,
    db: AsyncSessionDep,
) -> TokenResponse:
    """Verify a 6-digit email code and activate the user account.

    On success, the user's status transitions to ``active`` and a
    JWT access token is returned for immediate login.
    """
    normalized_email = payload.email.strip().lower()
    settings = get_email_verification_settings()
    cache = _get_auth_cache()

    submitted_hash = hash_verification_code(payload.code)
    result = await cache.verify_code(
        email=normalized_email,
        submitted_code_hash=submitted_hash,
        max_attempts=settings.max_attempts,
    )

    if result == -1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification code expired or not found. Please request a new code.",
        )
    if result == -2:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Maximum verification attempts exceeded. Please request a new code.",
        )
    if result == -3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification code.",
        )

    # result is the user_id
    user = await UserRepository.get_user_by_id(db, result)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User account not found.",
        )

    # Activate user
    if user.status != PENDING_VERIFICATION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This verification code is no longer valid.",
        )
    try:
        user = await UserRepository.activate_user(db, user)
    except ValueError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This verification code is no longer valid.",
        )

    # Issue JWT
    _ensure_user_can_authenticate(user)
    return await _issue_session(user, db, request, response)


@router.post("/resend-verification", status_code=status.HTTP_200_OK)
@limiter.limit("3/minute")
async def resend_verification(
    request: Request,
    payload: ResendVerificationRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSessionDep,
) -> dict:
    """Resend a verification code with cooldown and rate-limit enforcement."""
    normalized_email = canonicalize_email(payload.email)
    settings = get_email_verification_settings()
    cache = _get_auth_cache()

    # Verify user exists and is pending
    user = await UserRepository.get_user_by_email(db, normalized_email)
    if user is None or user.status != PENDING_VERIFICATION:
        # Uniform response to prevent enumeration
        return {
            "message": "If a pending account exists, a new verification code has been sent."
        }

    # Both limits are reservations, not check-then-set sequences.  This
    # guarantees that concurrent requests cannot dispatch multiple resends.
    if not await cache.reserve_resend_cooldown(
        normalized_email, settings.resend_cooldown_seconds
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Please wait before requesting another verification code.",
        )
    if not await cache.reserve_email_send(normalized_email, settings.hourly_limit):
        await cache.delete_cooldown(normalized_email)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Verification email limit reached. Please try again later.",
        )

    # Generate and store new code
    code = generate_verification_code()
    code_hash = hash_verification_code(code)
    try:
        await cache.store_verification_code(
            email=normalized_email,
            code_hash=code_hash,
            user_id=user.id,
            ttl_seconds=settings.code_ttl_seconds,
        )
    except Exception:
        # Do not leave an artificial cooldown behind when the new code was
        # not persisted.  The rate event remains conservative and expires.
        await cache.delete_cooldown(normalized_email)
        raise

    # Dispatch
    await enqueue_email(
        db,
        kind="verification",
        recipient=normalized_email,
        secret=code,
        user_id=user.id,
        tenant_id=user.tenant_id,
        idempotency_key=f"verification:{user.id}:{code_hash}",
    )

    return {
        "message": "If a pending account exists, a new verification code has been sent."
    }


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
async def login_user(
    request: Request,
    payload: UserLoginRequest,
    response: Response,
    db: AsyncSessionDep,
) -> TokenResponse:
    """Authenticate email & password and return a signed JWT access token.

    Security controls:
        * Timing sentinel absorbs Argon2id cost for non-existent users.
        * Pending-verification accounts are rejected with 403.
        * Transparent bcrypt → Argon2id hash upgrade on success.
    """
    normalized_email = canonicalize_email(payload.email)
    user = await UserRepository.get_user_by_email(db, normalized_email)

    if user is not None and user.status == SUSPENDED:
        await bounded_verify_timing_sentinel(payload.password)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is unavailable.",
        )

    if user is not None and user.hashed_password is None:
        # SSO-only user attempting password login
        identities = await ExternalIdentityRepository.get_all_by_user_id(db, user.id)
        if identities:
            provider = identities[0].provider.capitalize()
            # Still absorb timing
            await bounded_verify_timing_sentinel(payload.password)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"This email is already registered using {provider} Login. Please sign in with {provider}.",
            )

        # User has no password and no identities (invalid state)
        await bounded_verify_timing_sentinel(payload.password)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    if user is None:
        await bounded_verify_timing_sentinel(payload.password)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    # Check account status before verifying password
    if user.status == PENDING_VERIFICATION:
        # Still absorb timing cost
        await bounded_verify_timing_sentinel(payload.password)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account email not verified. Please check your email for the verification code.",
        )

    # Verify password with dual-algorithm support
    assert user.hashed_password is not None, (
        "Password hash cannot be None at this point"
    )
    valid, upgraded_hash = await bounded_verify_password(
        payload.password, user.hashed_password
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Transparent hash upgrade (bcrypt → Argon2id)
    if upgraded_hash is not None:
        await UserRepository.update_hashed_password_silent(db, user, upgraded_hash)

    # Generate token with sub set to email and full_name for sidebar display
    return await _issue_session(user, db, request, response)


@router.get("/me", response_model=UserResponse)
async def get_my_profile(
    tenant: Annotated[TenantContext, Depends(get_tenant_context)],
) -> UserResponse:
    """Return profile details only while the current tenant membership is active."""
    return UserResponse.model_validate(tenant.user)


@router.post("/api-key")
async def create_api_key(
    current_user: Annotated[
        TenantContext, Depends(require_permission("api_keys:manage"))
    ],
    db: AsyncSessionDep,
    name: str = "ingestion-key",
    expires_in_days: int = 90,
) -> dict:
    """Create a tenant-scoped ingestion key; plaintext is returned exactly once."""
    from datetime import datetime, timedelta, timezone

    if not 1 <= expires_in_days <= 365:
        raise HTTPException(
            status_code=422, detail="expires_in_days must be between 1 and 365"
        )
    # Serialize the per-tenant count and insert so concurrent creators cannot
    # both pass the active-key limit check.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"logsentinel:api-key-limit:{current_user.tenant_id}"},
    )
    active_rows = await db.execute(
        select(IngestionApiKeyRecord.id).where(
            IngestionApiKeyRecord.tenant_id == current_user.tenant_id,
            IngestionApiKeyRecord.user_id == current_user.user_id,
            IngestionApiKeyRecord.revoked_at.is_(None),
            IngestionApiKeyRecord.expires_at > datetime.now(timezone.utc),
        )
    )
    if len(active_rows.all()) >= 25:
        raise HTTPException(status_code=409, detail="active_api_key_limit_exceeded")
    raw_key = f"lsn_live_{secrets.token_urlsafe(32)}"
    record = IngestionApiKeyRecord(
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        key_prefix=raw_key[:16],
        key_hash=hashlib.sha256(raw_key.encode("utf-8")).hexdigest(),
        expires_at=datetime.now(timezone.utc) + timedelta(days=expires_in_days),
        scopes=["logs:ingest"],
    )
    db.add(record)
    await db.flush()
    await db.commit()
    return {"api_key": raw_key, "key_prefix": record.key_prefix, "id": record.id}


@router.get("/api-key")
async def list_api_keys(
    current_user: Annotated[
        TenantContext, Depends(require_permission("api_keys:manage"))
    ],
    db: AsyncSessionDep,
) -> dict:
    """List metadata only; existing plaintext keys are never retrievable."""
    result = await db.execute(
        select(IngestionApiKeyRecord)
        .where(IngestionApiKeyRecord.tenant_id == current_user.tenant_id)
        .where(IngestionApiKeyRecord.user_id == current_user.user_id)
        .order_by(IngestionApiKeyRecord.created_at.desc())
    )
    return {
        "keys": [
            {
                "id": record.id,
                "key_prefix": record.key_prefix,
                "created_at": record.created_at,
                "last_used_at": record.last_used_at,
                "expires_at": record.expires_at,
                "revoked": record.revoked_at is not None,
            }
            for record in result.scalars().all()
        ]
    }


@router.delete("/api-key/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: int,
    current_user: Annotated[
        TenantContext, Depends(require_permission("api_keys:manage"))
    ],
    db: AsyncSessionDep,
) -> None:
    """Revoke one key immediately, scoped by the authenticated tenant."""
    result = await db.execute(
        select(IngestionApiKeyRecord).where(
            IngestionApiKeyRecord.id == key_id,
            IngestionApiKeyRecord.tenant_id == current_user.tenant_id,
            IngestionApiKeyRecord.user_id == current_user.user_id,
            IngestionApiKeyRecord.revoked_at.is_(None),
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="api_key_not_found")
    from datetime import datetime, timezone

    record.revoked_at = datetime.now(timezone.utc)
    await db.commit()


@router.post("/api-key/{key_id}/rotate")
async def rotate_api_key(
    key_id: int,
    current_user: Annotated[
        TenantContext, Depends(require_permission("api_keys:manage"))
    ],
    db: AsyncSessionDep,
) -> dict:
    """Atomically revoke a tenant key and return its replacement once."""
    # Rotation also creates an active key; serialize it with ordinary key
    # creation so the per-tenant active-key limit remains race-safe.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"logsentinel:api-key-limit:{current_user.tenant_id}"},
    )
    result = await db.execute(
        select(IngestionApiKeyRecord)
        .where(
            IngestionApiKeyRecord.id == key_id,
            IngestionApiKeyRecord.tenant_id == current_user.tenant_id,
            IngestionApiKeyRecord.user_id == current_user.user_id,
            IngestionApiKeyRecord.revoked_at.is_(None),
        )
        .with_for_update()
    )
    old_record = result.scalar_one_or_none()
    if old_record is None:
        raise HTTPException(status_code=404, detail="api_key_not_found")
    from datetime import datetime, timedelta, timezone

    raw_key = f"lsn_live_{secrets.token_urlsafe(32)}"
    old_record.revoked_at = datetime.now(timezone.utc)
    new_record = IngestionApiKeyRecord(
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        key_prefix=raw_key[:16],
        key_hash=hashlib.sha256(raw_key.encode("utf-8")).hexdigest(),
        expires_at=datetime.now(timezone.utc) + timedelta(days=90),
        # Never turn a legacy key with absent scope metadata into a wildcard
        # or implicitly privileged replacement.
        scopes=list(old_record.scopes or []),
    )
    db.add(new_record)
    await db.flush()
    await db.commit()
    return {
        "api_key": raw_key,
        "key_prefix": new_record.key_prefix,
        "id": new_record.id,
    }


# ─── Google SSO ──────────────────────────────────────────────────────────────


@router.post("/google", response_model=TokenResponse)
@limiter.limit("100/minute")
async def google_login(
    request: Request,
    payload: GoogleLoginRequest,
    response: Response,
    db: AsyncSessionDep,
) -> TokenResponse:
    """Verify a Google id_token and return a LogSentinel JWT.

    If the user doesn't exist yet, a new account is automatically created.
    """
    import httpx

    if not GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google SSO is not configured. Set the GOOGLE_CLIENT_ID environment variable.",
        )

    provider_issuer = "https://accounts.google.com"
    provider_subject: str | None = None
    try:
        timeout = httpx.Timeout(10.0, connect=3.0, read=7.0, write=5.0, pool=3.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            if payload.credential.startswith("ya29."):
                # Validate access token
                resp = await client.post(
                    "https://oauth2.googleapis.com/tokeninfo",
                    data={"access_token": payload.credential},
                )
                if resp.status_code != 200:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid Google access token",
                    )
                token_info = resp.json()
                if not isinstance(token_info, dict):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid Google access token",
                    )
                if token_info.get("aud") != GOOGLE_CLIENT_ID:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Google credential audience mismatch",
                    )
                expires_in = token_info.get("expires_in")
                if (
                    not isinstance(expires_in, (int, str))
                    or not str(expires_in).isdigit()
                    or int(expires_in) <= 0
                ):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid Google access-token expiry",
                    )

                # Fetch user profile using the access token
                user_resp = await client.get(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    headers={"Authorization": f"Bearer {payload.credential}"},
                )
                if user_resp.status_code != 200:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Failed to retrieve Google user profile",
                    )
                idinfo = user_resp.json()
                if not isinstance(idinfo, dict):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid Google user profile",
                    )
            else:
                # Validate id_token
                resp = await client.post(
                    "https://oauth2.googleapis.com/tokeninfo",
                    data={"id_token": payload.credential},
                )
                if resp.status_code != 200:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid Google id_token",
                    )
                idinfo = resp.json()
                if not isinstance(idinfo, dict):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid Google id_token",
                    )
                if idinfo.get("aud") != GOOGLE_CLIENT_ID:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Google credential audience mismatch",
                    )
                if idinfo.get("iss") not in {"accounts.google.com", provider_issuer}:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Google credential issuer mismatch",
                    )
                exp = idinfo.get("exp")
                if (
                    not isinstance(exp, (int, float))
                    or exp <= datetime.now(timezone.utc).timestamp()
                ):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Expired Google id_token",
                    )
                provider_issuer = str(idinfo["iss"])
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Failed to verify Google credential",
        )

    provider_subject = idinfo.get("sub") if isinstance(idinfo.get("sub"), str) else None
    email_value = idinfo.get("email")
    if (
        not isinstance(email_value, str)
        or not email_value
        or len(email_value) > 320
        or not provider_subject
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google identity claims are incomplete",
        )
    if not idinfo.get("email_verified", False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google account email is not verified",
        )

    email = canonicalize_email(email_value)
    full_name_value = idinfo.get("name")
    full_name: str | None = (
        full_name_value
        if isinstance(full_name_value, str) and full_name_value
        else None
    )
    if full_name is not None:
        full_name = full_name[:255]

    # Find or create user
    ext_identity = await ExternalIdentityRepository.get_by_provider_identity(
        db,
        provider="google",
        issuer=provider_issuer,
        subject=provider_subject,
    )
    if ext_identity is None:
        alt_iss = (
            "accounts.google.com"
            if provider_issuer == "https://accounts.google.com"
            else "https://accounts.google.com"
        )
        ext_identity = await ExternalIdentityRepository.get_by_provider_identity(
            db,
            provider="google",
            issuer=alt_iss,
            subject=provider_subject,
        )

    if ext_identity is not None:
        user = await UserRepository.get_user_by_id(db, ext_identity.user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="google_identity_conflict",
            )
        mapped_tenant_id, _ = await resolve_provider_tenant_mapping(
            db,
            provider="google",
            issuer=ext_identity.issuer,
            provider_tenant_id=ext_identity.tenant_id or "",
        )
        if mapped_tenant_id != user.tenant_id:
            raise HTTPException(status_code=403, detail="provider_tenant_mismatch")
    else:
        user = await UserRepository.get_user_by_email(db, email)
        if user is not None:
            # User exists but has no google external identity (could be standard signup or other SSO)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This email is already connected to another sign-in method. Please sign in with your original method.",
            )

        try:
            user = await UserRepository.create_user(
                db=db,
                email=email,
                hashed_password=None,
                full_name=full_name,
                status=ACTIVE,
                commit=False,
                provider="google",
                issuer=provider_issuer,
                provider_tenant_id=idinfo.get("hd"),
                provider_subject=provider_subject,
                verified_email=email,
            )
            external_identity = (
                await ExternalIdentityRepository.create_external_identity(
                    db=db,
                    user_id=user.id,
                    provider="google",
                    issuer=provider_issuer,
                    subject=provider_subject,
                    tenant_id=idinfo.get("hd"),
                    email=email,
                    display_name=full_name,
                    commit=False,
                )
            )
            await db.commit()
            await db.refresh(user)
        except IntegrityError:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="google_identity_conflict",
            )
        except Exception as exc:
            await db.rollback()
            logger.error(
                "Google identity persistence failed: exception_type=%s detail=%s",
                type(exc).__name__,
                sanitize_error_text(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Google authentication is temporarily unavailable",
            ) from None
        logger.info("Auto-created user via Google SSO: user_id=%s", user.id)

    _ensure_user_can_authenticate(user)
    return await _issue_session(user, db, request, response)


# ─── Forgot Password ────────────────────────────────────────────────────────


@router.post("/forgot-password", status_code=status.HTTP_200_OK)
@limiter.limit("3/minute")
async def forgot_password(
    request: Request,
    payload: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSessionDep,
) -> dict:
    """Accept a password-reset request without exposing account existence.

    Always returns a success-shaped response regardless of whether the email
    exists. Timing is normalised to prevent enumeration via latency.
    """
    normalized_email = canonicalize_email(payload.email)
    cache = _get_auth_cache()
    # Rate-limit by email as well as the route's IP limiter.  A denied request
    # keeps the same generic response to preserve anti-enumeration behavior.
    if not await cache.reserve_email_action(normalized_email, "forgot", limit=3):
        await bounded_verify_timing_sentinel("forgot-password-timing")
        return {
            "message": "If an account with that email exists, a password reset link has been sent."
        }

    user = await UserRepository.get_user_by_email(db, normalized_email)

    if user is not None and user.status == ACTIVE:
        logger.info("Password reset requested for user_id=%s", user.id)

        # Generate opaque reset token
        settings = get_password_reset_settings()
        raw_token, token_hash = generate_reset_token()

        # PostgreSQL durably records the capability and encrypted delivery
        # work together. Valkey remains only a rate-limit/cache dependency.
        try:
            await issue_password_reset(
                db,
                user=user,
                token_digest=token_hash,
                raw_token=raw_token,
                ttl_seconds=settings.token_ttl_seconds,
            )
        except PasswordResetUnavailableError:
            logger.error("Password reset issuance unavailable")
    else:
        # Normalise timing: simulate cryptographic and I/O work
        await bounded_verify_timing_sentinel("forgot-password-timing")

    # Generic response to prevent email enumeration
    return {
        "message": "If an account with that email exists, a password reset link has been sent."
    }


# ─── Reset Password ─────────────────────────────────────────────────────────


@router.post("/reset-password", status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
async def reset_password(
    request: Request,
    payload: ResetPasswordRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSessionDep,
) -> dict:
    """Complete a PostgreSQL-authoritative single-use reset capability.

    Post-reset operations:
        * password_changed_at is set → all existing JWTs are invalidated.
        * A security notification email is dispatched.
    """
    token_hash = digest_reset_token(payload.token)
    try:
        await complete_password_reset(
            db,
            token_digest=token_hash,
            new_password=payload.new_password,
        )
    except PasswordResetInvalidError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token. Please request a new password reset.",
        ) from None
    except PasswordResetUnavailableError:
        # A failed/ambiguous database commit is never permission to retry the
        # mutation blindly. The durable row determines whether a later retry
        # is a completion replay or a still-issued capability.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password reset could not be completed. Request a new reset link.",
        ) from None

    return {
        "message": "Password has been reset successfully. You can now sign in with your new password."
    }


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("20/minute")
async def refresh_session(
    request: Request, response: Response, db: AsyncSessionDep
) -> TokenResponse:
    """Rotate the HttpOnly refresh token and return a fresh short-lived access JWT."""
    csrf = _validate_cookie_request(request)
    raw = request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw:
        raise HTTPException(status_code=401, detail="session_required")
    rotated = await rotate_refresh_token(db, raw, csrf)
    if rotated is None:
        response.delete_cookie(REFRESH_COOKIE_NAME, path="/api/auth")
        response.delete_cookie(CSRF_COOKIE_NAME, path="/")
        raise HTTPException(status_code=401, detail="invalid_or_reused_refresh_token")
    user, issued = rotated
    _set_session_cookies(response, request, issued)
    return TokenResponse(
        access_token=create_access_token(
            {"sub": user.email, "full_name": user.full_name or ""},
            session_id=issued.session_id,
        )
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout_session(
    request: Request, response: Response, db: AsyncSessionDep
) -> Response:
    _validate_cookie_request(request)
    await revoke_session(db, request.cookies.get(REFRESH_COOKIE_NAME))
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/api/auth")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ─── Microsoft SSO ───────────────────────────────────────────────────────────

# Lazy-initialized verifier — created on first use to pick up env at startup
_microsoft_verifier: MicrosoftTokenVerifier | None = None


def _get_microsoft_verifier() -> MicrosoftTokenVerifier:
    """Return a cached MicrosoftTokenVerifier instance."""
    global _microsoft_verifier
    if _microsoft_verifier is None:
        try:
            settings = get_microsoft_auth_settings()
        except (ValidationError, ValueError):
            raise MicrosoftAuthDisabledError()
        if not settings.enabled:
            raise MicrosoftAuthDisabledError()
        _microsoft_verifier = MicrosoftTokenVerifier(settings)
    return _microsoft_verifier


@router.post("/microsoft", response_model=TokenResponse)
@limiter.limit("100/minute")
async def microsoft_login(
    request: Request,
    payload: MicrosoftLoginRequest,
    response: Response,
    db: AsyncSessionDep,
) -> TokenResponse:
    """Verify a Microsoft access token and return a LogSentinel JWT.

    The Microsoft access token must have been issued for the LogSentinel
    API audience (``AZURE_CLIENT_ID``) with the required delegated scope
    (``AZURE_REQUIRED_SCOPE``).  The token is verified cryptographically
    against Microsoft's public JWKS endpoint.

    If the Microsoft identity is new, a LogSentinel user is provisioned
    automatically.  If a matching email already belongs to an existing
    local or Google user, an account-linking conflict is returned rather
    than silently merging.
    """
    # ── Verify the Microsoft access token ──────────────────────────────
    try:
        verifier = _get_microsoft_verifier()
        identity = await asyncio.to_thread(verifier.verify, payload.access_token)
    except MicrosoftAuthDisabledError:
        logger.warning("Microsoft login rejected: authentication is disabled")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="microsoft_auth_disabled",
        )
    except InvalidMicrosoftTenantError:
        logger.warning("Microsoft login rejected: invalid tenant")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid_microsoft_tenant",
        )
    except MissingRequiredScopeError:
        logger.warning("Microsoft login rejected: required scope missing")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="missing_required_scope",
        )
    except MicrosoftJWKSUnavailableError:
        logger.warning("Microsoft login unavailable: JWKS lookup failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="microsoft_jwks_unavailable",
        )
    except InvalidMicrosoftTokenError as exc:
        logger.warning(
            "Microsoft login rejected: exception_type=%s", type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_microsoft_token",
        )
    except MicrosoftAuthError as exc:
        logger.warning(
            "Microsoft login rejected: exception_type=%s", type(exc).__name__
        )
        # Catch-all for any other Microsoft auth error subclass
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_microsoft_token",
        )

    # ── Look up existing external identity ─────────────────────────────
    ext_identity = await ExternalIdentityRepository.get_by_provider_identity(
        db,
        provider="microsoft",
        issuer=identity.issuer,
        subject=identity.subject,
    )

    if ext_identity is not None:
        # Returning user — verify consistency
        user = await UserRepository.get_user_by_id(db, ext_identity.user_id)
        if user is None:
            # Orphaned external identity — should not happen in normal operation
            logger.error(
                "Orphaned external identity id=%d for provider=microsoft",
                ext_identity.id,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="microsoft_identity_conflict",
            )
        mapped_tenant_id, _ = await resolve_provider_tenant_mapping(
            db,
            provider="microsoft",
            issuer=identity.issuer,
            provider_tenant_id=identity.tenant_id,
        )
        if mapped_tenant_id != user.tenant_id:
            raise HTTPException(status_code=403, detail="provider_tenant_mismatch")
        _ensure_user_can_authenticate(user)
        return await _issue_session(user, db, request, response)

    # ── New Microsoft identity — provision user ────────────────────────
    candidate_email = identity.email.strip().lower() if identity.email else None

    if not candidate_email:
        # No usable email from Microsoft claims — cannot satisfy the
        # current users schema which requires a non-null email.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="microsoft_onboarding_required",
        )

    # Check for existing user with the same email
    existing_user = await UserRepository.get_user_by_email(db, candidate_email)
    if existing_user is not None:
        # An account already exists with this email under a different
        # provider.  We do NOT silently merge — the user must explicitly
        # link accounts (future feature).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="account_linking_required",
        )

    # Flush the user and external identity in one transaction. A failure in
    # either write must never leave an orphaned passwordless user.
    try:
        new_user = await UserRepository.create_user(
            db=db,
            email=candidate_email,
            hashed_password=None,  # SSO users have no local password
            full_name=identity.display_name,
            status=ACTIVE,
            commit=False,
            provider="microsoft",
            issuer=identity.issuer,
            provider_tenant_id=identity.tenant_id,
            provider_subject=identity.subject,
            verified_email=candidate_email,
        )
        external_identity = await ExternalIdentityRepository.create_external_identity(
            db=db,
            user_id=new_user.id,
            provider="microsoft",
            issuer=identity.issuer,
            subject=identity.subject,
            tenant_id=identity.tenant_id,
            provider_object_id=identity.object_id,
            email=candidate_email,
            display_name=identity.display_name,
            commit=False,
        )
        await db.commit()
        await db.refresh(new_user)
        await db.refresh(external_identity)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="microsoft_identity_conflict",
        )
    except Exception:
        await db.rollback()
        raise

    logger.info("Auto-created user via Microsoft SSO: user_id=%d", new_user.id)

    return await _issue_session(new_user, db, request, response)


# ─── GitHub SSO ──────────────────────────────────────────────────────────────


@router.get("/github")
@limiter.limit("30/minute")
async def github_login_redirect(request: Request):
    """Redirect user to GitHub OAuth authorization page."""
    settings = get_github_auth_settings()
    if not settings.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub SSO is not configured.",
        )

    # Validate the frontend origin to prevent redirect URL poisoning
    allowed_origins = [
        o.strip() for o in os.getenv("FRONTEND_URL", "http://localhost:8080").split(",")
    ]
    referer = request.headers.get("referer")
    frontend_origin = allowed_origins[0]
    if referer:
        parsed = urllib.parse.urlparse(referer)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in allowed_origins:
            frontend_origin = origin

    state = secrets.token_urlsafe(32)
    github_auth_url = "https://github.com/login/oauth/authorize"
    params = {
        "client_id": settings.client_id,
        "redirect_uri": settings.callback_url,
        "scope": "read:user user:email",
        "state": state,
    }
    url = f"{github_auth_url}?{urllib.parse.urlencode(params)}"
    response = RedirectResponse(url)
    response.set_cookie(
        key="github_oauth_state",
        value=state,
        httponly=True,
        max_age=600,
        secure=settings.callback_url.startswith("https"),
        samesite="lax",
    )
    response.set_cookie(
        key="github_oauth_origin",
        value=frontend_origin,
        httponly=True,
        max_age=600,
        secure=settings.callback_url.startswith("https"),
        samesite="lax",
    )
    return response


@router.get("/callback/github")
@limiter.limit("100/minute")
async def github_login_callback(
    request: Request,
    code: Annotated[str, Query(min_length=1, max_length=2048)],
    state: Annotated[str, Query(min_length=1, max_length=512)],
    db: AsyncSessionDep,
):
    """Handle GitHub OAuth callback, exchange code for token, and authenticate user."""
    settings = get_github_auth_settings()
    if not settings.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub SSO is not configured.",
        )

    cookie_state = request.cookies.get("github_oauth_state")
    if not state or not cookie_state or not secrets.compare_digest(state, cookie_state):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid state token (CSRF check failed)",
        )

    # 1. Exchange code for access token
    token_url = "https://github.com/login/oauth/access_token"
    timeout = httpx.Timeout(10.0, connect=3.0, read=7.0, write=5.0, pool=3.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            token_res = await client.post(
                token_url,
                headers={"Accept": "application/json"},
                data={
                    "client_id": settings.client_id,
                    "client_secret": settings.client_secret,
                    "code": code,
                    "redirect_uri": settings.callback_url,
                },
            )
            if token_res.status_code != 200:
                raise HTTPException(
                    status_code=400, detail="Failed to retrieve GitHub access token"
                )

            token_data = token_res.json()
            if not isinstance(token_data, dict):
                raise HTTPException(
                    status_code=400, detail="Invalid GitHub token response"
                )
            access_token_value = token_data.get("access_token")
            if (
                not isinstance(access_token_value, str)
                or not access_token_value
                or len(access_token_value) > 2048
            ):
                logger.error(
                    "GitHub token exchange failed: response_shape=%s",
                    type(token_data).__name__,
                )
                raise HTTPException(
                    status_code=400, detail="GitHub OAuth exchange failed"
                )
            access_token = access_token_value

            # 2. Fetch user profile
            user_res = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
            if user_res.status_code != 200:
                raise HTTPException(
                    status_code=400, detail="Failed to retrieve GitHub user profile"
                )

            github_user = user_res.json()
            if not isinstance(github_user, dict):
                raise HTTPException(
                    status_code=400, detail="Invalid GitHub user profile"
                )
            raw_github_id = github_user.get("id")
            if isinstance(raw_github_id, int) and raw_github_id > 0:
                github_id = str(raw_github_id)
            elif (
                isinstance(raw_github_id, str)
                and raw_github_id.isdigit()
                and 0 < len(raw_github_id) <= 32
            ):
                github_id = raw_github_id
            else:
                raise HTTPException(status_code=400, detail="Invalid GitHub identity")
            full_name_value = github_user.get("name") or github_user.get("login")
            full_name = (
                full_name_value[:255]
                if isinstance(full_name_value, str) and full_name_value
                else None
            )

            # 3. Fetch primary email
            email_res = await client.get(
                "https://api.github.com/user/emails",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
            if email_res.status_code != 200:
                raise HTTPException(
                    status_code=400, detail="Failed to retrieve GitHub emails"
                )

            emails = email_res.json()
            if not isinstance(emails, list):
                raise HTTPException(
                    status_code=400, detail="Invalid GitHub email response"
                )
            primary_email_value = next(
                (
                    entry.get("email")
                    for entry in emails
                    if isinstance(entry, dict)
                    and entry.get("primary") is True
                    and entry.get("verified") is True
                    and isinstance(entry.get("email"), str)
                    and len(entry["email"]) <= 320
                ),
                None,
            )
            if not isinstance(primary_email_value, str) or not primary_email_value:
                raise HTTPException(
                    status_code=400, detail="No primary email found on GitHub account"
                )
            primary_email = canonicalize_email(primary_email_value)
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        logger.error("GitHub provider boundary failed: exception_category=provider")
        raise HTTPException(
            status_code=502, detail="GitHub authentication is unavailable"
        ) from None

    # 4. Handle Account Linking & Unified User Logic
    account = await AccountRepository.get_account_by_provider(db, "github", github_id)
    if account is not None:
        user = await UserRepository.get_user_by_id(db, account.user_id)
        if user is None:
            raise HTTPException(
                status_code=409, detail="GitHub identity conflict: user missing"
            )
    else:
        user = await UserRepository.get_user_by_email(db, primary_email)
        if user is not None:
            # Email conflict: User exists but not linked to this GitHub account
            frontend_url = (
                request.cookies.get("github_oauth_origin")
                or os.getenv("FRONTEND_URL", "http://localhost:8080")
                .split(",")[-1]
                .strip()
            )
            error_msg = urllib.parse.quote(
                "This email is already registered using a different provider. Please sign in with your primary method."
            )
            return RedirectResponse(
                f"{frontend_url}/login?error={error_msg}", status_code=303
            )

        # New User
        try:
            user = await UserRepository.create_user(
                db=db,
                email=primary_email,
                hashed_password=None,
                full_name=full_name,
                status=ACTIVE,
                commit=False,
                provider="github",
                issuer="https://github.com",
                provider_tenant_id=None,
                provider_subject=github_id,
                verified_email=primary_email,
            )
            await AccountRepository.create_account(
                db=db,
                user_id=user.id,
                provider="github",
                provider_account_id=github_id,
                access_token=access_token,
                commit=False,
            )
            await db.commit()
            await db.refresh(user)
        except IntegrityError:
            await db.rollback()
            raise HTTPException(
                status_code=409, detail="GitHub identity conflict during creation"
            )
        except Exception as exc:
            await db.rollback()
            logger.error(
                "GitHub identity persistence failed: exception_type=%s detail=%s",
                type(exc).__name__,
                sanitize_error_text(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="GitHub authentication is temporarily unavailable",
            ) from None

        logger.info("Auto-created user via GitHub SSO: user_id=%s", user.id)

    # 5. Establish the same server-controlled cookie session used by local/other SSO.
    _ensure_user_can_authenticate(user)
    issued = await create_session(db, user)

    allowed_origins = [
        o.strip() for o in os.getenv("FRONTEND_URL", "http://localhost:8080").split(",")
    ]
    cookie_origin = request.cookies.get("github_oauth_origin")
    frontend_url = (
        cookie_origin if cookie_origin in allowed_origins else allowed_origins[0]
    )

    redirect = RedirectResponse(
        f"{frontend_url}/login?session=restored", status_code=303
    )
    _set_session_cookies(redirect, request, issued)
    redirect.delete_cookie("github_oauth_state")
    redirect.delete_cookie("github_oauth_origin")
    return redirect
