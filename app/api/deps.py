"""
VIGIL-AI Cameroun — FastAPI Dependencies
Reusable dependency injection for auth, RBAC, DB, and pagination
"""
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.exceptions import (
    AccountInactiveError,
    AccountLockedError,
    ForbiddenError,
    InvalidTokenError,
)
from app.core.security import decode_token
from app.database import get_db
from app.models.user import User

# ── Bearer Token Security ─────────────────────────────────────
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """
    Extract and validate JWT from Authorization header.
    Returns the authenticated User object.
    """
    if not credentials:
        raise InvalidTokenError()

    try:
        payload = decode_token(credentials.credentials)
    except JWTError:
        raise InvalidTokenError()

    if payload.get("type") != "access":
        raise InvalidTokenError()

    user_id_str: str | None = payload.get("sub")
    if not user_id_str:
        raise InvalidTokenError()

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise InvalidTokenError()

    # Fetch user with role eagerly loaded
    result = await db.execute(
        select(User)
        .options(selectinload(User.role))
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise InvalidTokenError()
    if not user.is_active:
        raise AccountInactiveError()
    if user.is_locked:
        raise AccountLockedError()

    return user


# ── Role-Based Access Helpers ─────────────────────────────────
def require_role(*allowed_roles: str):
    """Dependency factory that enforces role-based access control."""
    async def role_check(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if current_user.role_name not in allowed_roles:
            raise ForbiddenError(f"access this resource (requires: {', '.join(allowed_roles)})")
        return current_user
    return role_check


# ── Pre-built Role Dependencies ───────────────────────────────
require_admin = require_role("admin")
require_analyst = require_role("admin", "analyst")
require_any = require_role("admin", "analyst", "viewer")


# ── Pagination ────────────────────────────────────────────────
@dataclass
class PaginationParams:
    page: int = 1
    page_size: int = 20

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


async def get_pagination(
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
) -> PaginationParams:
    return PaginationParams(page=page, page_size=page_size)


# ── Type Aliases ──────────────────────────────────────────────
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
AnalystUser = Annotated[User, Depends(require_analyst)]
AnyAuthUser = Annotated[User, Depends(require_any)]
DBSession = Annotated[AsyncSession, Depends(get_db)]
Pagination = Annotated[PaginationParams, Depends(get_pagination)]
