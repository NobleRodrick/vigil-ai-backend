"""
VIGIL-AI Cameroun — Security Core
JWT token creation/validation, password hashing, and account security utilities
"""
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

# ── Password Context ──────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


def hash_password(password: str) -> str:
    """Hash a password with bcrypt (cost factor 12)."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)


def validate_password_strength(password: str) -> tuple[bool, str]:
    """
    Validate password meets minimum requirements:
    - At least 10 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    - At least one special character
    """
    if len(password) < 10:
        return False, "Password must be at least 10 characters long"
    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter"
    if not any(c.islower() for c in password):
        return False, "Password must contain at least one lowercase letter"
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one digit"
    if not any(c in "!@#$%^&*()_+-=[]{}|;':\",./<>?" for c in password):
        return False, "Password must contain at least one special character"
    return True, "OK"


# ── JWT Tokens ────────────────────────────────────────────────
def create_access_token(
    user_id: uuid.UUID,
    role: str,
    additional_claims: dict[str, Any] | None = None,
) -> str:
    """Create a short-lived JWT access token."""
    expire = datetime.now(UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "exp": expire,
        "iat": datetime.now(UTC),
        "jti": str(uuid.uuid4()),  # JWT ID for potential revocation
    }
    if additional_claims:
        payload.update(additional_claims)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(user_id: uuid.UUID) -> str:
    """Create a long-lived JWT refresh token."""
    expire = datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": "refresh",
        "exp": expire,
        "iat": datetime.now(UTC),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """
    Decode and validate a JWT token.
    Raises JWTError if invalid or expired.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError as e:
        raise JWTError(f"Token validation failed: {e}") from e


# ── Password Reset Tokens ─────────────────────────────────────
def generate_reset_token() -> tuple[str, str]:
    """
    Generate a secure password reset token.
    Returns (plain_token, hashed_token).
    Store only the hash in the database.
    """
    plain_token = secrets.token_urlsafe(32)
    hashed_token = hashlib.sha256(plain_token.encode()).hexdigest()
    return plain_token, hashed_token


def hash_reset_token(plain_token: str) -> str:
    """Hash a plain reset token for database comparison."""
    return hashlib.sha256(plain_token.encode()).hexdigest()


# ── Token Expiry Helpers ──────────────────────────────────────
def get_reset_token_expiry() -> datetime:
    """Password reset tokens expire in 60 minutes."""
    return datetime.now(UTC) + timedelta(minutes=60)


def is_token_expired(expiry: datetime) -> bool:
    """Check if a token expiry timestamp has passed."""
    if expiry.tzinfo is None:
        # Handle naive datetime from DB
        expiry = expiry.replace(tzinfo=UTC)
    return datetime.now(UTC) > expiry
