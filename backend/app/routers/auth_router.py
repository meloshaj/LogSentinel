"""FastAPI router for user authentication endpoints.

Provides routes for user registration, token generation (login),
user profile retrieval, Google SSO, Microsoft SSO, and password reset.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError
from sqlalchemy.exc import IntegrityError

from ..core.database import AsyncSessionDep
from ..core.orm import UserRecord
from ..core.settings import get_github_auth_settings, get_microsoft_auth_settings
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

logger = logging.getLogger("logsentinel.auth_router")

from ..services.email import send_password_reset_email

# Google OAuth configuration
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")

router = APIRouter(prefix="/api/auth", tags=["authentication"])


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


# ─── Endpoint Route Handlers ─────────────────────────────────────────────────

from ..core.rate_limit import limiter


@router.post(
    "/register", status_code=status.HTTP_201_CREATED, response_model=UserResponse
)
@limiter.limit("3/minute")
async def register_user(
    request: Request,
    payload: UserRegisterRequest,
    db: AsyncSessionDep,
) -> UserResponse:
    """Register a new user account and save their hashed credentials in the database."""
    # Check if a user already exists with this email
    existing_user = await UserRepository.get_user_by_email(db, payload.email)
    if existing_user is not None:
        identities = await ExternalIdentityRepository.get_all_by_user_id(
            db, existing_user.id
        )
        if identities:
            # User has an OAuth identity, so standard signup should not be allowed
            provider = identities[0].provider.capitalize()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"This email is already registered using {provider} Login. Please sign in with {provider}.",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A user with this email address already exists",
            )

    # Hash the password and save
    hashed = hash_password(payload.password)
    user = await UserRepository.create_user(
        db=db,
        email=payload.email,
        hashed_password=hashed,
        full_name=payload.fullName,
        organization=payload.organization,
    )

    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
async def login_user(
    request: Request,
    payload: UserLoginRequest,
    db: AsyncSessionDep,
) -> TokenResponse:
    """Authenticate email & password and return a signed JWT access token."""
    user = await UserRepository.get_user_by_email(db, payload.email)
    if user is not None and not user.hashed_password:
        identities = await ExternalIdentityRepository.get_all_by_user_id(db, user.id)
        if identities:
            provider = identities[0].provider.capitalize()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"This email is already registered using {provider} Login. Please sign in with {provider}.",
            )

    if (
        user is None
        or not user.hashed_password
        or not verify_password(payload.password, user.hashed_password)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

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
                    headers={"Authorization": f"Bearer {payload.credential}"}
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
    exists to prevent email enumeration attacks.
    """
    from datetime import datetime, timedelta, timezone

    import jwt as pyjwt

    user = await UserRepository.get_user_by_email(db, payload.email)

    if user is not None:
        logger.info(
            "Password reset requested for user_id=%s",
            user.id,
        )

        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
        to_encode = {"sub": user.email, "type": "password_reset", "exp": expire}
        token = pyjwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

        background_tasks.add_task(send_password_reset_email, user.email, token)

    # Generic response to prevent email enumeration
    return {
        "message": "If an account with that email exists, a password reset link has been sent."
    }


# ─── Reset Password ─────────────────────────────────────────────────────────


@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    payload: ResetPasswordRequest,
    db: AsyncSessionDep,
) -> dict:
    """Verify the reset token and update the user's password."""
    import jwt as pyjwt

    try:
        token_data = pyjwt.decode(
            payload.token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM]
        )
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset link has expired. Please request a new one.",
        )
    except pyjwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid reset token.",
        )

    # Ensure this is a password-reset token, not a regular session token
    if token_data.get("type") != "password_reset":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid reset token.",
        )

    email = token_data.get("sub")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid reset token.",
        )

    user = await UserRepository.get_user_by_email(db, email)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    hashed = hash_password(payload.new_password)
    await UserRepository.update_password(db, user, hashed)

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
