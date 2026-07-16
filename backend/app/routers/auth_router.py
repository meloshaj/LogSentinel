"""FastAPI router for user authentication endpoints.

Provides routes for user registration, token generation (login), and
user profile retrieval.
"""

from __future__ import annotations

import logging
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
)
from ..core.orm import UserRecord

logger = logging.getLogger("logsentinel.auth_router")

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
    if user is None or not verify_password(payload.password, user.hashed_password):
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
