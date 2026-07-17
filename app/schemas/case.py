"""
VIGIL-AI Cameroun — Case Management Schemas
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# ── Case Note Schemas ─────────────────────────────────────────
class CaseNoteCreate(BaseModel):
    content: str = Field(min_length=5, max_length=10000)


class CaseNoteResponse(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    content: str
    created_at: datetime
    author_full_name: str | None
    author_email: str | None

    model_config = {"from_attributes": True}

    @classmethod
    def from_note(cls, note: object) -> "CaseNoteResponse":
        return cls(
            id=note.id,  # type: ignore
            case_id=note.case_id,  # type: ignore
            content=note.content,  # type: ignore
            created_at=note.created_at,  # type: ignore
            author_full_name=note.author.full_name if note.author else None,  # type: ignore
            author_email=note.author.email if note.author else None,  # type: ignore
        )


# ── Case History ──────────────────────────────────────────────
class CaseHistoryResponse(BaseModel):
    id: uuid.UUID
    field_changed: str
    old_value: str | None
    new_value: str
    changed_at: datetime
    changed_by_name: str | None

    model_config = {"from_attributes": True}


# ── Case Status Update ────────────────────────────────────────
class CaseStatusUpdate(BaseModel):
    status: str = Field(pattern="^(in_review|resolved|archived)$")
    resolution_summary: str | None = Field(default=None, max_length=5000)


class CaseAssignUpdate(BaseModel):
    analyst_id: uuid.UUID | None = None  # None = unassign


class CaseEscalateRequest(BaseModel):
    reason: str = Field(min_length=20, max_length=2000)


# ── Case Response ─────────────────────────────────────────────
class CaseSummaryResponse(BaseModel):
    """Compact case info for list views."""
    id: uuid.UUID
    status: str
    priority: str
    is_escalated: bool
    created_at: datetime
    updated_at: datetime
    # Submission info
    case_number: str
    content_type: str
    # Analysis info
    risk_score: int | None
    classification: str | None
    # Assignment info
    assignee_name: str | None

    model_config = {"from_attributes": True}


class CaseDetailResponse(BaseModel):
    """Full case detail for the case detail page."""
    id: uuid.UUID
    status: str
    priority: str
    is_escalated: bool
    escalation_reason: str | None
    resolution_summary: str | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # Submission
    submission_id: uuid.UUID
    case_number: str
    content_type: str
    content_text_preview: str | None
    content_url: str | None
    file_name: str | None
    source_url: str | None
    analyst_notes: str | None
    language: str | None
    submitted_at: datetime
    submitter_name: str | None
    # Analysis
    risk_score: int | None
    classification: str | None
    confidence: float | None
    explanation_fr: str | None
    explanation_en: str | None
    engine_used: str | None
    processing_time_ms: int | None
    analyzed_at: datetime | None
    key_indicators: list[str] | None = None
    sub_scores: dict[str, float] | None = None
    # People
    assignee_id: uuid.UUID | None
    assignee_name: str | None
    creator_name: str | None
    # Sub-resources
    notes: list[CaseNoteResponse]
    history: list[CaseHistoryResponse]

    model_config = {"from_attributes": True}


class PaginatedCases(BaseModel):
    items: list[CaseSummaryResponse]
    total_count: int
    page: int
    page_size: int
    total_pages: int


# ── Alert Schema ──────────────────────────────────────────────
class AlertResponse(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    alert_type: str
    message: str
    is_read: bool
    sent_at: datetime

    model_config = {"from_attributes": True}
