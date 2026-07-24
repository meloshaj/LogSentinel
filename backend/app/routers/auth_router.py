"""FastAPI router for user authentication endpoints.

Provides routes for user registration, token generation (login),
user profile retrieval, Google SSO, and password reset.
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from ..core.database import AsyncSessionDep
from ..repositories.user_repository import UserRepository
from ..security.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
    JWT_SECRET_KEY,
    JWT_ALGORITHM,
)
from ..core.orm import UserRecord

logger = logging.getLogger("logsentinel.auth_router")

# Google OAuth configuration
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")

router = APIRouter(prefix="/api/auth", tags=["authentication"])


# ─── Request/Response Schemas ────────────────────────────────────────────────

class UserRegisterRequest(BaseModel):
    """Schema for user registration request."""
    email: EmailStr
    password: str = Field(..., min_length=8, description="Password must be at least 8 characters long")
    fullName: Optional[str] = Field(None, description="Optional full name of the user")
    organization: Optional[str] = Field(None, description="Optional organization name")

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
    full_name: Optional[str]
    organization: Optional[str]

    model_config = ConfigDict(from_attributes=True)


class GoogleLoginRequest(BaseModel):
    """Schema for Google SSO login — accepts the id_token from the frontend."""
    credential: str


class ForgotPasswordRequest(BaseModel):
    """Schema for requesting a password reset email."""
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Schema for resetting the password with a valid token."""
    token: str
    new_password: str = Field(..., min_length=8, description="New password must be at least 8 characters long")


# ─── Endpoint Route Handlers ─────────────────────────────────────────────────

@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
async def register_user(
    payload: UserRegisterRequest,
    db: AsyncSessionDep,
) -> UserResponse:
    """Register a new user account and save their hashed credentials in the database."""
    # Check if a user already exists with this email
    existing_user = await UserRepository.get_user_by_email(db, payload.email)
    if existing_user is not None:
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
async def login_user(
    payload: UserLoginRequest,
    db: AsyncSessionDep,
) -> TokenResponse:
    """Authenticate email & password and return a signed JWT access token."""
    user = await UserRepository.get_user_by_email(db, payload.email)
    if user is None or not user.hashed_password or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Generate token with sub set to email
    token = create_access_token(data={"sub": user.email})
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
async def get_my_profile(
    current_user: Annotated[UserRecord, Depends(get_current_user)],
) -> UserResponse:
    """Return the profile details of the authenticated user."""
    return UserResponse.model_validate(current_user)


# ─── Google SSO ──────────────────────────────────────────────────────────────

@router.post("/google", response_model=TokenResponse)
async def google_login(
    payload: GoogleLoginRequest,
    db: AsyncSessionDep,
) -> TokenResponse:
    """Verify a Google id_token and return a LogSentinel JWT.

    If the user doesn't exist yet, a new account is automatically created.
    """
    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests

    if not GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google SSO is not configured. Set the GOOGLE_CLIENT_ID environment variable.",
        )

    try:
        idinfo = google_id_token.verify_oauth2_token(
            payload.credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google credential",
        )

    email: str = idinfo.get("email", "")
    if not email or not idinfo.get("email_verified", False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google account email is not verified",
        )

    full_name: str | None = idinfo.get("name")

    # Find or create user
    user = await UserRepository.get_user_by_email(db, email)
    if user is None:
        user = await UserRepository.create_user(
            db=db,
            email=email,
            hashed_password=None,
            full_name=full_name,
        )
        logger.info("Auto-created user via Google SSO: %s", email)

    token = create_access_token(data={"sub": user.email})
    return TokenResponse(access_token=token)


# ─── Forgot Password ────────────────────────────────────────────────────────

@router.post("/forgot-password", status_code=status.HTTP_200_OK)
async def forgot_password(
    payload: ForgotPasswordRequest,
    db: AsyncSessionDep,
) -> dict:
    """Generate a password-reset token and log the reset link.

    Always returns a success-shaped response regardless of whether the email
    exists to prevent email enumeration attacks.
    """
    import jwt as pyjwt

    user = await UserRepository.get_user_by_email(db, payload.email)

    if user is not None:
        # Create a short-lived token (15 minutes) with a reset-specific claim
        reset_token = create_access_token(
            data={"sub": user.email, "type": "password_reset"},
            expires_delta=timedelta(minutes=15),
        )

        # In production you would send an email here.
        # For now we log the link to the console so it can be used locally.
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
        reset_link = f"{frontend_url}/reset-password?token={reset_token}"
        logger.info(
            "──── PASSWORD RESET LINK ────\n"
            "  Email : %s\n"
            "  Link  : %s\n"
            "─────────────────────────────",
            user.email,
            reset_link,
        )
        # Also print directly to ensure visibility in Docker logs
        print(
            f"\n{'='*50}\n"
            f"  PASSWORD RESET LINK\n"
            f"  Email : {user.email}\n"
            f"  Link  : {reset_link}\n"
            f"{'='*50}\n",
            flush=True,
        )

    # Generic response to prevent email enumeration
    return {"message": "If an account with that email exists, a password reset link has been sent."}


# ─── Reset Password ─────────────────────────────────────────────────────────

@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    payload: ResetPasswordRequest,
    db: AsyncSessionDep,
) -> dict:
    """Verify the reset token and update the user's password."""
    import jwt as pyjwt

    try:
        token_data = pyjwt.decode(payload.token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
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

    return {"message": "Password has been reset successfully. You can now sign in with your new password."}

