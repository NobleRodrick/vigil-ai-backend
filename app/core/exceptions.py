"""
VIGIL-AI Cameroun — Custom Exceptions & Error Handlers
Standardized error responses across the entire API
"""
from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse


class VigilAIException(Exception):
    """Base exception for VIGIL-AI application errors."""
    def __init__(self, message: str, code: str = "INTERNAL_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)


# ── Auth Exceptions ───────────────────────────────────────────
class AuthException(VigilAIException):
    pass


class InvalidCredentialsError(AuthException):
    def __init__(self):
        super().__init__("Invalid email or password", "INVALID_CREDENTIALS")


class AccountLockedError(AuthException):
    def __init__(self, minutes: int = 30):
        super().__init__(
            f"Account is temporarily locked. Try again in {minutes} minutes.",
            "ACCOUNT_LOCKED",
        )


class AccountInactiveError(AuthException):
    def __init__(self):
        super().__init__("Account is deactivated. Contact your administrator.", "ACCOUNT_INACTIVE")


class TokenExpiredError(AuthException):
    def __init__(self):
        super().__init__("Token has expired", "TOKEN_EXPIRED")


class InvalidTokenError(AuthException):
    def __init__(self):
        super().__init__("Invalid or malformed token", "INVALID_TOKEN")


# ── Authorization Exceptions ──────────────────────────────────
class ForbiddenError(VigilAIException):
    def __init__(self, action: str = "perform this action"):
        super().__init__(
            f"You do not have permission to {action}",
            "FORBIDDEN",
        )


# ── Resource Exceptions ───────────────────────────────────────
class NotFoundError(VigilAIException):
    def __init__(self, resource: str = "Resource"):
        super().__init__(f"{resource} not found", "NOT_FOUND")


class ConflictError(VigilAIException):
    def __init__(self, message: str):
        super().__init__(message, "CONFLICT")


class ValidationError(VigilAIException):
    def __init__(self, message: str):
        super().__init__(message, "VALIDATION_ERROR")


# ── File Upload Exceptions ────────────────────────────────────
class FileTooLargeError(VigilAIException):
    def __init__(self, max_mb: int):
        super().__init__(f"File exceeds maximum size of {max_mb}MB", "FILE_TOO_LARGE")


class InvalidFileTypeError(VigilAIException):
    def __init__(self, allowed_types: list[str]):
        super().__init__(
            f"Invalid file type. Allowed: {', '.join(allowed_types)}",
            "INVALID_FILE_TYPE",
        )


# ── AI Engine Exceptions ──────────────────────────────────────
class AIEngineError(VigilAIException):
    def __init__(self, detail: str = "Analysis service temporarily unavailable"):
        super().__init__(detail, "AI_ENGINE_ERROR")


# ── HTTP Exception Factory ────────────────────────────────────
def http_error(status_code: int, detail: str, code: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"message": detail, "code": code},
    )


# ── Exception → HTTP Status Mapping ──────────────────────────
EXCEPTION_STATUS_MAP: dict[type, int] = {
    InvalidCredentialsError: status.HTTP_401_UNAUTHORIZED,
    AccountLockedError: status.HTTP_401_UNAUTHORIZED,
    AccountInactiveError: status.HTTP_401_UNAUTHORIZED,
    TokenExpiredError: status.HTTP_401_UNAUTHORIZED,
    InvalidTokenError: status.HTTP_401_UNAUTHORIZED,
    ForbiddenError: status.HTTP_403_FORBIDDEN,
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    ValidationError: status.HTTP_422_UNPROCESSABLE_ENTITY,
    FileTooLargeError: status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    InvalidFileTypeError: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    AIEngineError: status.HTTP_503_SERVICE_UNAVAILABLE,
}


# ── Global Exception Handler ──────────────────────────────────
async def vigil_exception_handler(request: Request, exc: VigilAIException) -> JSONResponse:
    status_code = EXCEPTION_STATUS_MAP.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)
    return JSONResponse(
        status_code=status_code,
        content={"detail": exc.message, "code": exc.code},
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": "HTTP_ERROR"},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An unexpected error occurred", "code": "INTERNAL_ERROR"},
    )
