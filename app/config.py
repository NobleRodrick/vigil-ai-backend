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

    # ── Hugging Face Inference API (FREE tier) ────────────────
    # Get a free token at https://huggingface.co/settings/tokens
    # Used as the second detection tier when Gemini is unavailable or
    # rate-limited. Each setting is a comma-separated list of model IDs
    # tried in order; results from responsive models are ensembled.
    HF_API_KEY: str = ""
    HF_API_BASE: str = "https://router.huggingface.co/hf-inference/models"
    HF_API_FALLBACK_BASE: str = "https://api-inference.huggingface.co/models"
    HF_TIMEOUT_SECONDS: float = 40.0
    HF_TEXT_AI_MODELS: str = (
        "Hello-SimpleAI/chatgpt-detector-roberta,"
        "openai-community/roberta-base-openai-detector"
    )
    HF_FAKE_NEWS_MODELS: str = "hamzab/roberta-fake-news-classification"
    HF_IMAGE_DEEPFAKE_MODELS: str = (
        "dima806/deepfake_vs_real_image_detection,"
        "prithivMLmods/Deep-Fake-Detector-v2-Model"
    )
    HF_AUDIO_DEEPFAKE_MODELS: str = "MelodyMachine/Deepfake-audio-detection-V2"

    @property
    def HF_TEXT_AI_MODEL_LIST(self) -> List[str]:
        return [m.strip() for m in self.HF_TEXT_AI_MODELS.split(",") if m.strip()]

    @property
    def HF_FAKE_NEWS_MODEL_LIST(self) -> List[str]:
        return [m.strip() for m in self.HF_FAKE_NEWS_MODELS.split(",") if m.strip()]

    @property
    def HF_IMAGE_DEEPFAKE_MODEL_LIST(self) -> List[str]:
        return [m.strip() for m in self.HF_IMAGE_DEEPFAKE_MODELS.split(",") if m.strip()]

    @property
    def HF_AUDIO_DEEPFAKE_MODEL_LIST(self) -> List[str]:
        return [m.strip() for m in self.HF_AUDIO_DEEPFAKE_MODELS.split(",") if m.strip()]

    # ── Cloudinary (optional media hosting — FREE tier) ───────
    # Either set CLOUDINARY_URL=cloudinary://<api_key>:<api_secret>@<cloud_name>
    # or the three individual values. When configured, uploaded media is
    # mirrored to Cloudinary so files survive ephemeral-disk restarts.
    CLOUDINARY_URL: str = ""
    CLOUDINARY_CLOUD_NAME: str = ""
    CLOUDINARY_API_KEY: str = ""
    CLOUDINARY_API_SECRET: str = ""
    CLOUDINARY_MAX_MB: int = 10  # only mirror files up to this size
    CLOUDINARY_FOLDER: str = "vigil-ai"

    # ── Remote media fetching (URL submissions) ───────────────
    URL_FETCH_MAX_MB: int = 25          # cap for direct media downloads
    URL_FETCH_TIMEOUT_SECONDS: float = 60.0
    YTDLP_ENABLED: bool = True          # use yt-dlp for platform video URLs
    YTDLP_MAX_FILESIZE_MB: int = 60
    YTDLP_MAX_DURATION_SECONDS: int = 600

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
    # 0 = every analyzed submission becomes a reviewable case (full audit
    # coverage); raise it to only open cases for higher-risk content.
    AUTO_CREATE_CASE_MIN_SCORE: int = 0


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
