"""
VIGIL-AI Cameroun — Authentication Router
POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
POST /api/v1/auth/password-reset/request
POST /api/v1/auth/password-reset/confirm
GET  /api/v1/auth/me
"""
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Request
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DBSession
from app.config import settings
from app.core.exceptions import (
    AccountInactiveError,
    AccountLockedError,
    ConflictError,
    InvalidCredentialsError,
    InvalidTokenError,
    NotFoundError,
    ValidationError,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_reset_token,
    get_reset_token_expiry,
    hash_password,
    hash_reset_token,
    is_token_expired,
    verify_password,
)
from app.models.audit_log import AuditAction, AuditLog
from app.models.user import User
from app.schemas.user import (
    ChangePasswordRequest,
    LoginRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = logging.getLogger(__name__)


@router.post("/login", response_model=dict, summary="Login and receive JWT tokens")
async def login(
    payload: LoginRequest,
    request: Request,
    db: DBSession,
    background_tasks: BackgroundTasks,
):
    """
    Authenticate with email and password.
    Returns access token (60 min) and refresh token (7 days).
    """
    ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")

    # Find user
    result = await db.execute(
        select(User).options(selectinload(User.role)).where(User.email == payload.email)
    )
    user = result.scalar_one_or_none()

    # Constant-time check to prevent timing attacks
    if not user:
        logger.warning(f"Login attempt for unknown email: {payload.email} from {ip}")
        raise InvalidCredentialsError()

    # Check lockout
    if user.is_locked:
        if user.locked_until and not is_token_expired(user.locked_until):
            minutes_left = int(
                (user.locked_until.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds() / 60
            )
            raise AccountLockedError(max(1, minutes_left))
        else:
            # Lockout expired — unlock
            user.is_locked = False
            user.failed_login_attempts = 0
            user.locked_until = None

    # Check active
    if not user.is_active:
        raise AccountInactiveError()

    # Verify password
    if not verify_password(payload.password, user.password_hash):
        user.failed_login_attempts += 1
        logger.warning(f"Failed login for {payload.email}: attempt {user.failed_login_attempts}")

        # Lock after max failed attempts
        if user.failed_login_attempts >= settings.MAX_FAILED_LOGIN_ATTEMPTS:
            from datetime import timedelta
            user.is_locked = True
            user.locked_until = datetime.now(UTC) + timedelta(
                minutes=settings.ACCOUNT_LOCKOUT_MINUTES
            )
            logger.warning(f"Account locked: {payload.email}")
            audit = AuditLog(
                user_id=user.id, action=AuditAction.USER_LOCKED,
                ip_address=ip, user_agent=user_agent,
            )
            db.add(audit)

        audit = AuditLog(
            user_id=user.id, action=AuditAction.USER_LOGIN_FAILED,
            ip_address=ip, user_agent=user_agent,
            details={"attempt": user.failed_login_attempts},
        )
        db.add(audit)
        await db.commit()
        raise InvalidCredentialsError()

    # Success — reset failed attempts
    user.failed_login_attempts = 0
    user.is_locked = False
    user.last_login_at = datetime.now(UTC)

    # Generate tokens
    access_token = create_access_token(user.id, user.role.name)
    refresh_token = create_refresh_token(user.id)

    # Audit log
    audit = AuditLog(
        user_id=user.id, action=AuditAction.USER_LOGIN,
        ip_address=ip, user_agent=user_agent,
    )
    db.add(audit)
    await db.commit()

    logger.info(f"Successful login: {user.email} (role={user.role.name}) from {ip}")

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": UserResponse.model_validate(user),
    }


@router.post("/refresh", response_model=TokenResponse, summary="Refresh access token")
async def refresh_token(payload: RefreshRequest, db: DBSession):
    """Exchange a valid refresh token for a new access token."""
    from jose import JWTError
    try:
        token_data = decode_token(payload.refresh_token)
    except JWTError:
        raise InvalidTokenError()

    if token_data.get("type") != "refresh":
        raise InvalidTokenError()

    import uuid
    user_id = uuid.UUID(token_data["sub"])
    result = await db.execute(
        select(User).options(selectinload(User.role)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise InvalidTokenError()

    new_access = create_access_token(user.id, user.role.name)
    new_refresh = create_refresh_token(user.id)

    return TokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/logout", summary="Logout (client-side token invalidation)")
async def logout(current_user: CurrentUser, db: DBSession, request: Request):
    """
    Logout endpoint.
    In a stateless JWT system, tokens are invalidated client-side.
    This endpoint logs the logout event for audit purposes.
    """
    ip = request.client.host if request.client else "unknown"
    audit = AuditLog(
        user_id=current_user.id,
        action=AuditAction.USER_LOGOUT,
        ip_address=ip,
    )
    db.add(audit)
    return {"message": "Logged out successfully"}


@router.post(
    "/password-reset/request",
    summary="Request a password reset email",
)
async def request_password_reset(payload: PasswordResetRequest, db: DBSession):
    """
    Send a password reset link to the user's email.
    Always returns 200 regardless of whether the email exists (security).
    """
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    if user and user.is_active:
        plain_token, hashed_token = generate_reset_token()
        user.password_reset_token = hashed_token
        user.password_reset_expires = get_reset_token_expiry()

        audit = AuditLog(
            user_id=user.id, action=AuditAction.PASSWORD_RESET_REQUESTED
        )
        db.add(audit)

        # In development: log the token to console
        reset_url = f"{settings.FRONTEND_URL}/reset-password?token={plain_token}"
        logger.info(
            f"\n{'='*60}\n"
            f"🔑 PASSWORD RESET LINK (development mode)\n"
            f"Email: {user.email}\n"
            f"Reset URL: {reset_url}\n"
            f"{'='*60}"
        )

    return {"message": "If that email exists, a reset link has been sent."}


@router.post(
    "/password-reset/confirm",
    summary="Set a new password using a reset token",
)
async def confirm_password_reset(payload: PasswordResetConfirm, db: DBSession):
    """Confirm a password reset with the token received via email."""
    from app.core.security import validate_password_strength

    ok, msg = validate_password_strength(payload.new_password)
    if not ok:
        raise ValidationError(msg)

    hashed_token = hash_reset_token(payload.token)
    result = await db.execute(
        select(User).where(User.password_reset_token == hashed_token)
    )
    user = result.scalar_one_or_none()

    if not user or not user.password_reset_expires:
        raise InvalidTokenError()

    if is_token_expired(user.password_reset_expires):
        raise InvalidTokenError()

    # Set new password
    user.password_hash = hash_password(payload.new_password)
    user.password_reset_token = None
    user.password_reset_expires = None
    user.failed_login_attempts = 0
    user.is_locked = False

    audit = AuditLog(user_id=user.id, action=AuditAction.PASSWORD_RESET_COMPLETED)
    db.add(audit)

    logger.info(f"Password reset completed for {user.email}")
    return {"message": "Password reset successful. You can now log in with your new password."}


@router.get("/me", response_model=UserResponse, summary="Get current user profile")
async def get_me(current_user: CurrentUser):
    """Return the profile of the currently authenticated user."""
    return current_user


@router.post("/change-password", summary="Change your own password")
async def change_password(
    payload: ChangePasswordRequest,
    current_user: CurrentUser,
    db: DBSession,
):
    """Change password (requires current password verification)."""
    from app.core.security import validate_password_strength

    if not verify_password(payload.current_password, current_user.password_hash):
        raise InvalidCredentialsError()

    ok, msg = validate_password_strength(payload.new_password)
    if not ok:
        raise ValidationError(msg)

    current_user.password_hash = hash_password(payload.new_password)
    audit = AuditLog(user_id=current_user.id, action=AuditAction.USER_UPDATED,
                     details={"field": "password"})
    db.add(audit)

    return {"message": "Password changed successfully."}
