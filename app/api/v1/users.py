"""
VIGIL-AI Cameroun — Users Router (Admin)
GET    /api/v1/users
POST   /api/v1/users
GET    /api/v1/users/{id}
PATCH  /api/v1/users/{id}
DELETE /api/v1/users/{id}
"""
import logging
import uuid

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import AdminUser, AnyAuthUser, CurrentUser, DBSession, Pagination
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.security import hash_password
from app.models.audit_log import AuditAction, AuditLog
from app.models.user import Role, User
from app.schemas.user import (
    PaginatedUsers,
    UserCreate,
    UserResponse,
    UserUpdate,
)

router = APIRouter(prefix="/users", tags=["User Management"])
logger = logging.getLogger(__name__)


async def _get_role_by_name(db, role_name: str) -> Role:
    result = await db.execute(select(Role).where(Role.name == role_name))
    role = result.scalar_one_or_none()
    if not role:
        raise ValidationError(f"Role '{role_name}' does not exist")
    return role


@router.get("/", response_model=PaginatedUsers, summary="List all users (Admin only)")
async def list_users(
    current_user: AdminUser,
    db: DBSession,
    pagination: Pagination,
    search: str | None = Query(default=None),
    role: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
):
    query = select(User).options(selectinload(User.role))

    if search:
        query = query.where(
            User.email.ilike(f"%{search}%") | User.full_name.ilike(f"%{search}%")
        )
    if role:
        query = query.join(User.role).where(Role.name == role)
    if is_active is not None:
        query = query.where(User.is_active == is_active)

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.order_by(User.created_at.desc()).offset(pagination.offset).limit(pagination.page_size)
    result = await db.execute(query)
    users = result.scalars().all()

    return PaginatedUsers(
        items=[UserResponse.model_validate(u) for u in users],
        total_count=total,
        page=pagination.page,
        page_size=pagination.page_size,
        total_pages=max(1, -(-total // pagination.page_size)),
    )


@router.post("/", response_model=UserResponse, status_code=201, summary="Create a new user")
async def create_user(
    payload: UserCreate,
    current_user: AdminUser,
    db: DBSession,
):
    # Check email uniqueness
    existing = (await db.execute(select(User).where(User.email == payload.email))).scalar_one_or_none()
    if existing:
        raise ConflictError(f"Email '{payload.email}' is already registered")

    role = await _get_role_by_name(db, payload.role_name)

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role_id=role.id,
        organization=payload.organization,
        preferred_language=payload.preferred_language,
    )
    db.add(user)

    audit = AuditLog(
        user_id=current_user.id,
        action=AuditAction.USER_CREATED,
        resource_type="user",
        details={"email": payload.email, "role": payload.role_name},
    )
    db.add(audit)
    await db.commit()
    await db.refresh(user)

    logger.info(f"User created: {user.email} (role={payload.role_name}) by {current_user.email}")
    return UserResponse.model_validate(user)


@router.get("/{user_id}", response_model=UserResponse, summary="Get a user's profile")
async def get_user(user_id: uuid.UUID, current_user: AdminUser, db: DBSession):
    result = await db.execute(
        select(User).options(selectinload(User.role)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("User")
    return UserResponse.model_validate(user)


@router.patch("/{user_id}", response_model=UserResponse, summary="Update a user")
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    current_user: AdminUser,
    db: DBSession,
):
    result = await db.execute(
        select(User).options(selectinload(User.role)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("User")

    changes = {}
    if payload.full_name is not None:
        user.full_name = payload.full_name
        changes["full_name"] = payload.full_name
    if payload.organization is not None:
        user.organization = payload.organization
        changes["organization"] = payload.organization
    if payload.preferred_language is not None:
        user.preferred_language = payload.preferred_language
    if payload.is_active is not None:
        user.is_active = payload.is_active
        changes["is_active"] = payload.is_active
    if payload.role_name is not None:
        role = await _get_role_by_name(db, payload.role_name)
        user.role_id = role.id
        changes["role"] = payload.role_name

    audit = AuditLog(
        user_id=current_user.id,
        action=AuditAction.USER_UPDATED,
        resource_type="user",
        resource_id=user_id,
        details=changes,
    )
    db.add(audit)
    await db.commit()
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.delete("/{user_id}", status_code=204, summary="Deactivate a user account")
async def deactivate_user(
    user_id: uuid.UUID,
    current_user: AdminUser,
    db: DBSession,
):
    if user_id == current_user.id:
        raise ValidationError("You cannot deactivate your own account")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("User")

    user.is_active = False

    audit = AuditLog(
        user_id=current_user.id,
        action=AuditAction.USER_DEACTIVATED,
        resource_type="user",
        resource_id=user_id,
        details={"email": user.email},
    )
    db.add(audit)
    logger.info(f"User {user.email} deactivated by {current_user.email}")
