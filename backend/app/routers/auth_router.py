"""FastAPI router for user authentication endpoints.

Provides routes for user registration with email verification,
token generation (login), user profile retrieval, Google SSO,
Microsoft SSO, GitHub SSO, email verification, password reset,
and resend verification.

Security controls:
    * Timing-attack normalization on login and forgot-password.
    * Atomic single-use verification codes via Valkey Lua scripts.
    * Atomic single-use password-reset tokens via Valkey Lua scripts.
    * Session invalidation via password_changed_at + JWT iat checks.
    * Rate limiting via SlowAPI on all sensitive endpoints.
    * Abuse prevention via per-email cooldowns and sliding-window limits.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select

from ..core.database import AsyncSessionDep
from ..core.orm import UserRecord
from ..core.settings import (
    get_email_verification_settings,
    get_github_auth_settings,
    get_microsoft_auth_settings,
    get_password_reset_settings,
)
from ..core.user_status import ACTIVE, PENDING_VERIFICATION
from ..repositories.account_repository import AccountRepository
from ..repositories.external_identity_repository import ExternalIdentityRepository
from ..repositories.user_repository import UserRepository
from ..security.auth import (
    JWT_ALGORITHM,
    JWT_SECRET_KEY,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
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
from ..services.email import (
    send_password_changed_notification,
    send_password_reset_email,
    send_verification_email,
)
from ..services.password import (
    generate_reset_token,
    generate_verification_code,
    hash_verification_code,
    bounded_hash_password,
    bounded_verify_password,
    bounded_verify_timing_sentinel,
)

logger = logging.getLogger("logsentinel.auth_router")

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
        from ..core.redis import _redis_pool
        from redis.asyncio import Redis

        if _redis_pool is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Cache service is not available",
            )
        _auth_cache = AuthCacheManager(Redis(connection_pool=_redis_pool))
    return _auth_cache


# ─── Request/Response Schemas ────────────────────────────────────────────────


class UserRegisterRequest(BaseModel):
    """Schema for user registration request."""

    email: EmailStr
    password: str = Field(
        ..., min_length=8, description="Password must be at least 8 characters long"
    )
    fullName: str | None = Field(None, description="Optional full name of the user")
    organization: str | None = Field(None, description="Optional organization name")

    model_config = ConfigDict(populate_by_name=True)


class UserLoginRequest(BaseModel):
    """Schema for user login request."""

    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """Schema for login response containing the JWT token."""

    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    """Schema for authenticated user profile details."""

    id: int
    email: str
    full_name: str | None
    organization: str | None

    model_config = ConfigDict(from_attributes=True)


class GoogleLoginRequest(BaseModel):
    """Schema for Google SSO login — accepts the id_token from the frontend."""

    credential: str


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

    token: str
    new_password: str = Field(
        ..., min_length=8, description="New password must be at least 8 characters long"
    )


class VerifyEmailRequest(BaseModel):
    """Schema for email verification code submission."""

    email: EmailStr
    code: str = Field(
        ..., min_length=6, max_length=6, pattern=r"^\d{6}$",
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

from ..core.rate_limit import limiter


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
    normalized_email = payload.email.strip().lower()
    
    # Use row-level lock to prevent TOCTOU race condition
    existing_user = await UserRepository.get_user_by_email_for_update(db, normalized_email)
    
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
            reserved = await cache.reserve_resend_cooldown(normalized_email, settings.resend_cooldown_seconds)
            if not reserved:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="A verification code was recently dispatched for this email. Please check your inbox or wait before retrying.",
                )

            # Re-registration of a pending user: update credentials and resend code
            from sqlalchemy.sql import func
            existing_user.hashed_password = await bounded_hash_password(payload.password)
            existing_user.full_name = payload.fullName or existing_user.full_name
            existing_user.organization = payload.organization or existing_user.organization
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
        await cache.reserve_resend_cooldown(normalized_email, settings.resend_cooldown_seconds)
        
        # New user
        hashed = await bounded_hash_password(payload.password)
        user = await UserRepository.create_user(
            db=db,
            email=normalized_email,
            hashed_password=hashed,
            full_name=payload.fullName,
            organization=payload.organization,
            status=PENDING_VERIFICATION,
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
        raise AuthCacheUnavailableError("Failed to persist verification code in cache.") from exc

    # Dispatch verification email
    background_tasks.add_task(send_verification_email, normalized_email, code)

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
    user = await UserRepository.activate_user(db, user)

    # Issue JWT
    token = create_access_token(
        data={"sub": user.email, "full_name": user.full_name or ""}
    )
    return TokenResponse(access_token=token)


@router.post("/resend-verification", status_code=status.HTTP_200_OK)
@limiter.limit("3/minute")
async def resend_verification(
    request: Request,
    payload: ResendVerificationRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSessionDep,
) -> dict:
    """Resend a verification code with cooldown and rate-limit enforcement."""
    normalized_email = payload.email.strip().lower()
    settings = get_email_verification_settings()
    cache = _get_auth_cache()

    # Cooldown check
    if await cache.check_resend_cooldown(normalized_email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Please wait before requesting another verification code.",
        )

    # Hourly rate check
    if await cache.check_hourly_rate(normalized_email, settings.hourly_limit):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Verification email limit reached. Please try again later.",
        )

    # Verify user exists and is pending
    user = await UserRepository.get_user_by_email(db, normalized_email)
    if user is None or user.status != PENDING_VERIFICATION:
        # Uniform response to prevent enumeration
        return {"message": "If a pending account exists, a new verification code has been sent."}

    # Generate and store new code
    code = generate_verification_code()
    code_hash = hash_verification_code(code)
    await cache.store_verification_code(
        email=normalized_email,
        code_hash=code_hash,
        user_id=user.id,
        ttl_seconds=settings.code_ttl_seconds,
    )

    # Set cooldown and record rate event
    await cache.set_resend_cooldown(normalized_email, settings.resend_cooldown_seconds)
    await cache.record_email_send(normalized_email)

    # Dispatch
    background_tasks.add_task(send_verification_email, normalized_email, code)

    return {"message": "If a pending account exists, a new verification code has been sent."}


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
async def login_user(
    request: Request,
    payload: UserLoginRequest,
    db: AsyncSessionDep,
) -> TokenResponse:
    """Authenticate email & password and return a signed JWT access token.

    Security controls:
        * Timing sentinel absorbs Argon2id cost for non-existent users.
        * Pending-verification accounts are rejected with 403.
        * Transparent bcrypt → Argon2id hash upgrade on success.
    """
    user = await UserRepository.get_user_by_email(db, payload.email)

    if user is not None and not user.hashed_password:
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
    valid, upgraded_hash = await bounded_verify_password(payload.password, user.hashed_password)
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
    token = create_access_token(
        data={"sub": user.email, "full_name": user.full_name or ""}
    )
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
async def get_my_profile(
    current_user: Annotated[UserRecord, Depends(get_current_user)],
) -> UserResponse:
    """Return the profile details of the authenticated user."""
    return UserResponse.model_validate(current_user)


@router.get("/api-key")
async def get_my_api_key(
    current_user: Annotated[UserRecord, Depends(get_current_user)],
) -> dict:
    """Return a mock API key for the authenticated user for the frontend to display."""
    return {"api_key": "lsn_test_sk_mock_fetched_from_backend"}


# ─── Google SSO ──────────────────────────────────────────────────────────────


@router.post("/google", response_model=TokenResponse)
async def google_login(
    payload: GoogleLoginRequest,
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

    try:
        async with httpx.AsyncClient() as client:
            if payload.credential.startswith("ya29."):
                # Validate access token
                resp = await client.get(
                    f"https://oauth2.googleapis.com/tokeninfo?access_token={payload.credential}"
                )
                if resp.status_code != 200:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid Google access token",
                    )
                token_info = resp.json()
                if token_info.get("aud") and token_info.get("aud") != GOOGLE_CLIENT_ID:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Google credential audience mismatch",
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
            else:
                # Validate id_token
                resp = await client.get(
                    f"https://oauth2.googleapis.com/tokeninfo?id_token={payload.credential}"
                )
                if resp.status_code != 200:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid Google id_token",
                    )
                idinfo = resp.json()
                if idinfo.get("aud") != GOOGLE_CLIENT_ID:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Google credential audience mismatch",
                    )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Failed to verify Google credential",
        )

    email: str = idinfo.get("email", "")
    if not email or not idinfo.get("email_verified", False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google account email is not verified",
        )

    full_name: str | None = idinfo.get("name")

    # Find or create user
    ext_identity = await ExternalIdentityRepository.get_by_provider_identity(
        db,
        provider="google",
        issuer="https://accounts.google.com",
        subject=idinfo.get("sub", ""),
    )

    if ext_identity is not None:
        user = await UserRepository.get_user_by_id(db, ext_identity.user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="google_identity_conflict",
            )
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
            )
            external_identity = (
                await ExternalIdentityRepository.create_external_identity(
                    db=db,
                    user_id=user.id,
                    provider="google",
                    issuer=idinfo.get("iss", "accounts.google.com"),
                    subject=idinfo.get("sub", ""),
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
        except Exception:
            await db.rollback()
            raise
        logger.info("Auto-created user via Google SSO: %s", email)

    token = create_access_token(
        data={"sub": user.email, "full_name": user.full_name or ""}
    )
    return TokenResponse(access_token=token)


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
    user = await UserRepository.get_user_by_email(db, payload.email)

    if user is not None and user.status == ACTIVE:
        logger.info("Password reset requested for user_id=%s", user.id)

        # Generate opaque reset token
        settings = get_password_reset_settings()
        raw_token, token_hash = generate_reset_token()

        # Store hash in Valkey (single-use via Lua on consumption)
        cache = _get_auth_cache()
        await cache.store_reset_token(
            token_hash=token_hash,
            user_id=user.id,
            ttl_seconds=settings.token_ttl_seconds,
        )

        background_tasks.add_task(send_password_reset_email, user.email, raw_token)
    else:
        # Normalise timing: simulate cryptographic and I/O work
        await asyncio.sleep(0.15 + random.uniform(0, 0.05))

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
    """Consume a single-use reset token and update the user's password.

    Post-reset operations:
        * password_changed_at is set → all existing JWTs are invalidated.
        * A security notification email is dispatched.
    """
    # Compute SHA-256 of the submitted raw token
    token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()

    # Atomic single-use consumption
    cache = _get_auth_cache()
    user_id = await cache.consume_reset_token(token_hash)

    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token. Please request a new password reset.",
        )

    user = await UserRepository.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token.",
        )

    # Generate new hash and update user
    hashed = await bounded_hash_password(payload.new_password)
    await UserRepository.update_password_with_timestamp(db, user, hashed)

    # Dispatch security notification
    background_tasks.add_task(send_password_changed_notification, user.email)

    return {
        "message": "Password has been reset successfully. You can now sign in with your new password."
    }


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
async def microsoft_login(
    payload: MicrosoftLoginRequest,
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
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="microsoft_auth_disabled",
        )
    except InvalidMicrosoftTenantError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid_microsoft_tenant",
        )
    except MissingRequiredScopeError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="missing_required_scope",
        )
    except MicrosoftJWKSUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="microsoft_jwks_unavailable",
        )
    except InvalidMicrosoftTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_microsoft_token",
        )
    except MicrosoftAuthError:
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
        token = create_access_token(
            data={"sub": user.email, "full_name": user.full_name or ""}
        )
        return TokenResponse(access_token=token)

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

    token = create_access_token(
        data={"sub": new_user.email, "full_name": new_user.full_name or ""}
    )
    return TokenResponse(access_token=token)


# ─── GitHub SSO ──────────────────────────────────────────────────────────────

import secrets
import urllib.parse

import httpx
from fastapi.responses import RedirectResponse


@router.get("/github")
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
async def github_login_callback(
    request: Request,
    code: str,
    state: str,
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
    if not state or not cookie_state or state != cookie_state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid state token (CSRF check failed)",
        )

    # 1. Exchange code for access token
    token_url = "https://github.com/login/oauth/access_token"
    async with httpx.AsyncClient() as client:
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
        access_token = token_data.get("access_token")
        if not access_token:
            gh_error = (
                token_data.get("error_description")
                or token_data.get("error")
                or "No access token returned from GitHub"
            )
            logger.error(
                "GitHub token exchange failed: %s (raw response: %s)",
                gh_error,
                token_data,
            )
            raise HTTPException(
                status_code=400, detail=f"GitHub OAuth error: {gh_error}"
            )

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
        github_id = str(github_user["id"])
        full_name = github_user.get("name") or github_user.get("login")

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
        primary_email = next(
            (e["email"] for e in emails if e.get("primary") and e.get("verified")), None
        )
        if not primary_email:
            raise HTTPException(
                status_code=400, detail="No primary email found on GitHub account"
            )

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
        except Exception:
            await db.rollback()
            raise

        logger.info("Auto-created user via GitHub SSO: %s", primary_email)

    # 5. Issue session token and redirect using URL fragment to avoid access log leakage
    token = create_access_token(
        data={"sub": user.email, "full_name": user.full_name or ""}
    )

    allowed_origins = [
        o.strip() for o in os.getenv("FRONTEND_URL", "http://localhost:8080").split(",")
    ]
    cookie_origin = request.cookies.get("github_oauth_origin")
    frontend_url = (
        cookie_origin if cookie_origin in allowed_origins else allowed_origins[0]
    )

    return RedirectResponse(f"{frontend_url}/login#token={token}", status_code=303)
