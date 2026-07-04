"""
VIGIL-AI Cameroun — Auth & User Schemas (Pydantic v2)
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


# ── Auth Schemas ──────────────────────────────────────────────
class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class RefreshRequest(BaseModel):
    refresh_token: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(min_length=10)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10)


# ── Role Schemas ──────────────────────────────────────────────
class RoleResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None

    model_config = {"from_attributes": True}


# ── User Schemas ──────────────────────────────────────────────
class UserBase(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    organization: str | None = Field(default=None, max_length=200)
    preferred_language: str = Field(default="fr", pattern="^(fr|en)$")


class UserCreate(UserBase):
    password: str = Field(min_length=10)
    role_name: str = Field(default="analyst", pattern="^(admin|analyst|viewer)$")

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        from app.core.security import validate_password_strength
        ok, msg = validate_password_strength(v)
        if not ok:
            raise ValueError(msg)
        return v


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    organization: str | None = Field(default=None, max_length=200)
    preferred_language: str | None = Field(default=None, pattern="^(fr|en)$")
    role_name: str | None = Field(default=None, pattern="^(admin|analyst|viewer)$")
    is_active: bool | None = None


class UserPublicProfile(BaseModel):
    """Minimal user info embedded in other responses."""
    id: uuid.UUID
    full_name: str
    email: str
    role_name: str
    organization: str | None

    model_config = {"from_attributes": True}

    @classmethod
    def from_user(cls, user: object) -> "UserPublicProfile":
        return cls(
            id=user.id,  # type: ignore
            full_name=user.full_name,  # type: ignore
            email=user.email,  # type: ignore
            role_name=user.role.name if user.role else "unknown",  # type: ignore
            organization=user.organization,  # type: ignore
        )


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    organization: str | None
    preferred_language: str
    is_active: bool
    is_locked: bool
    last_login_at: datetime | None
    created_at: datetime
    role: RoleResponse

    model_config = {"from_attributes": True}


class UserWithTokenResponse(BaseModel):
    user: UserResponse
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class PaginatedUsers(BaseModel):
    items: list[UserResponse]
    total_count: int
    page: int
    page_size: int
    total_pages: int
