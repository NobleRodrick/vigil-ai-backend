"""
VIGIL-AI Cameroun — Submission & Analysis Schemas
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl, field_validator


# ── Submission Schemas ────────────────────────────────────────
class TextSubmissionCreate(BaseModel):
    content_text: str = Field(min_length=10, max_length=50000)
    language: str | None = Field(default=None, pattern="^(fr|en|auto)$")
    source_url: str | None = None
    analyst_notes: str | None = Field(default=None, max_length=5000)


class VideoSubmissionCreate(BaseModel):
    content_url: str = Field(min_length=10)
    source_url: str | None = None
    analyst_notes: str | None = Field(default=None, max_length=5000)

    @field_validator("content_url")
    @classmethod
    def validate_video_url(cls, v: str) -> str:
        allowed_domains = [
            "youtube.com", "youtu.be", "facebook.com", "fb.watch",
            "twitter.com", "x.com", "tiktok.com", "dailymotion.com",
            "vimeo.com",
        ]
        from urllib.parse import urlparse
        parsed = urlparse(v)
        if not parsed.scheme in ("http", "https"):
            raise ValueError("URL must use HTTP or HTTPS")
        domain = parsed.netloc.lower().replace("www.", "")
        if not any(domain.endswith(allowed) for allowed in allowed_domains):
            raise ValueError(
                f"Video URL must be from an allowed platform: {', '.join(allowed_domains)}"
            )
        return v


# ── Analysis Schemas ──────────────────────────────────────────
class AnalysisResponse(BaseModel):
    id: uuid.UUID
    submission_id: uuid.UUID
    risk_score: int | None
    classification: str | None
    confidence: float | None
    explanation_fr: str | None
    explanation_en: str | None
    engine_used: str | None
    processing_time_ms: int | None
    analyzed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Submission Response Schemas ───────────────────────────────
class SubmissionResponse(BaseModel):
    id: uuid.UUID
    case_number: str
    content_type: str
    status: str
    language: str | None
    source_url: str | None
    analyst_notes: str | None
    file_name: str | None
    file_size_bytes: int | None
    content_text_preview: str | None  # First 500 chars only
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_submission(cls, sub: object) -> "SubmissionResponse":
        preview = None
        if sub.content_text:  # type: ignore
            preview = sub.content_text[:500] + "..." if len(sub.content_text) > 500 else sub.content_text  # type: ignore
        return cls(
            id=sub.id,  # type: ignore
            case_number=sub.case_number,  # type: ignore
            content_type=sub.content_type,  # type: ignore
            status=sub.status,  # type: ignore
            language=sub.language,  # type: ignore
            source_url=sub.source_url,  # type: ignore
            analyst_notes=sub.analyst_notes,  # type: ignore
            file_name=sub.file_name,  # type: ignore
            file_size_bytes=sub.file_size_bytes,  # type: ignore
            content_text_preview=preview,
            created_at=sub.created_at,  # type: ignore
            updated_at=sub.updated_at,  # type: ignore
        )


class SubmissionDetailResponse(SubmissionResponse):
    """Full submission detail including analysis result."""
    submitted_by_name: str | None
    analysis: AnalysisResponse | None
    content_url: str | None


class PaginatedSubmissions(BaseModel):
    items: list[SubmissionResponse]
    total_count: int
    page: int
    page_size: int
    total_pages: int


# ── Queue Confirmation ────────────────────────────────────────
class SubmissionQueuedResponse(BaseModel):
    case_number: str
    submission_id: uuid.UUID
    message: str
    status: str = "queued"
