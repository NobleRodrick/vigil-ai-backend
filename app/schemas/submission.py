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
        """Accept known platforms (fetched via yt-dlp) or any direct HTTP(S)
        media link (fetched by streaming download)."""
        from urllib.parse import urlparse
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("URL must be a valid HTTP or HTTPS link")
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
    # Extracted from raw_api_response for the UI
    key_indicators: list[str] | None = None
    sub_scores: dict[str, float] | None = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_analysis(cls, analysis: object) -> "AnalysisResponse":
        """Build from the ORM object, lifting indicators/sub-scores out of
        the stored raw engine response."""
        resp = cls.model_validate(analysis)
        raw = getattr(analysis, "raw_api_response", None) or {}
        if isinstance(raw, dict):
            indicators = raw.get("key_indicators")
            if isinstance(indicators, list):
                resp.key_indicators = [str(i) for i in indicators][:8]
            subs = raw.get("sub_scores")
            if isinstance(subs, dict):
                resp.sub_scores = {
                    str(k): float(v) for k, v in subs.items()
                    if isinstance(v, (int, float))
                }
        return resp


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
    # Verdict summary (populated when the analysis relationship is loaded)
    risk_score: int | None = None
    classification: str | None = None
    fake_news_probability: float | None = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_submission(cls, sub: object) -> "SubmissionResponse":
        preview = None
        if sub.content_text:  # type: ignore
            preview = sub.content_text[:500] + "..." if len(sub.content_text) > 500 else sub.content_text  # type: ignore

        # Analysis verdict — only if the relationship was eagerly loaded
        risk_score = classification = fake_news = None
        analysis = sub.__dict__.get("analysis")  # avoid triggering a lazy load
        if analysis is not None:
            risk_score = analysis.risk_score
            classification = analysis.classification
            raw = analysis.raw_api_response
            if isinstance(raw, dict):
                subs = raw.get("sub_scores")
                if isinstance(subs, dict) and isinstance(subs.get("fake_news"), (int, float)):
                    fake_news = float(subs["fake_news"])

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
            risk_score=risk_score,
            classification=classification,
            fake_news_probability=fake_news,
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
