"""
VIGIL-AI Cameroun — Application Configuration
All settings loaded from environment variables / .env file
"""
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ───────────────────────────────────────────
    APP_NAME: str = "VIGIL-AI Cameroun"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"
    APP_BASE_URL: str = "http://localhost:8000"
    # Used for building links that go INTO emails/notifications for users to click
    # (password reset, etc.) — must point at the frontend, not this API.
    FRONTEND_URL: str = "http://localhost:5173"

    # ── Security ──────────────────────────────────────────────
    SECRET_KEY: str = "changeme-use-openssl-rand-hex-32"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── Database ──────────────────────────────────────────────
    POSTGRES_USER: str = "vigilai"
    POSTGRES_PASSWORD: str = "vigilai_secure_pwd"
    POSTGRES_DB: str = "vigilai_db"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    DATABASE_URL: str = "postgresql+asyncpg://vigilai:vigilai_secure_pwd@localhost:5432/vigilai_db"

    # Sync URL used by Alembic
    @property
    def SYNC_DATABASE_URL(self) -> str:
        return self.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

    # ── Redis ─────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # ── File Storage ──────────────────────────────────────────
    UPLOAD_DIR: str = "./uploads"
    MAX_FILE_SIZE_MB: int = 100
    ALLOWED_IMAGE_TYPES: str = "image/jpeg,image/png,image/webp,image/gif"
    ALLOWED_AUDIO_TYPES: str = "audio/mpeg,audio/wav,audio/ogg,audio/mp3"
    ALLOWED_VIDEO_TYPES: str = "video/mp4,video/avi,video/quicktime"

    @property
    def ALLOWED_IMAGE_TYPE_LIST(self) -> List[str]:
        return [t.strip() for t in self.ALLOWED_IMAGE_TYPES.split(",")]

    @property
    def ALLOWED_AUDIO_TYPE_LIST(self) -> List[str]:
        return [t.strip() for t in self.ALLOWED_AUDIO_TYPES.split(",")]

    @property
    def ALLOWED_VIDEO_TYPE_LIST(self) -> List[str]:
        return [t.strip() for t in self.ALLOWED_VIDEO_TYPES.split(",")]

    @property
    def MAX_FILE_SIZE_BYTES(self) -> int:
        return self.MAX_FILE_SIZE_MB * 1024 * 1024

    # ── Google Gemini (FREE) ───────────────────────────────────
    # Get a free API key at https://aistudio.google.com/apikey
    # Free tier (gemini-2.0-flash): 15 requests/min, 1M tokens/min, 1500 requests/day
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.0-flash"
    GEMINI_API_BASE: str = "https://generativelanguage.googleapis.com/v1beta/models"
    # Max inline file size Gemini accepts without the File API (bytes)
    GEMINI_INLINE_FILE_LIMIT: int = 15 * 1024 * 1024  # 15MB

    # ── Email ─────────────────────────────────────────────────
    EMAIL_ENABLED: bool = False
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 1025
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "noreply@vigilai.cm"
    SMTP_USE_TLS: bool = False

    # ── CORS ──────────────────────────────────────────────────
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    @property
    def CORS_ORIGIN_LIST(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]

    # ── Rate Limiting ─────────────────────────────────────────
    RATE_LIMIT_PER_MINUTE: int = 60
    LOGIN_RATE_LIMIT_PER_MINUTE: int = 10
    MAX_FAILED_LOGIN_ATTEMPTS: int = 5
    ACCOUNT_LOCKOUT_MINUTES: int = 30

    # ── Risk Score Thresholds ─────────────────────────────────
    RISK_SCORE_SAFE_MAX: int = 29
    RISK_SCORE_SUSPICIOUS_MAX: int = 69
    AUTO_CREATE_CASE_MIN_SCORE: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
