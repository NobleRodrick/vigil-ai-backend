"""
VIGIL-AI Cameroun — Cases Router
GET    /api/v1/cases
GET    /api/v1/cases/{id}
PATCH  /api/v1/cases/{id}/status
PATCH  /api/v1/cases/{id}/assign
POST   /api/v1/cases/{id}/notes
GET    /api/v1/cases/{id}/notes
POST   /api/v1/cases/{id}/escalate
GET    /api/v1/cases/export
"""
import io
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import AdminUser, AnalystUser, AnyAuthUser, DBSession, Pagination
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.models.audit_log import AuditAction, AuditLog
from app.models.case import Alert, AlertChannel, Case, CaseHistory, CaseNote, CaseStatus
from app.models.submission import Submission
from app.models.user import User
from app.schemas.case import (
    CaseAssignUpdate,
    CaseDetailResponse,
    CaseEscalateRequest,
    CaseHistoryResponse,
    CaseNoteCreate,
    CaseNoteResponse,
    CaseSummaryResponse,
    CaseStatusUpdate,
    PaginatedCases,
)

router = APIRouter(prefix="/cases", tags=["Case Management"])
logger = logging.getLogger(__name__)


async def _get_case_with_details(case_id: uuid.UUID, db) -> Case:
    """Load a case with all relationships."""
    result = await db.execute(
        select(Case)
        .options(
            selectinload(Case.submission).selectinload(Submission.analysis),
            selectinload(Case.submission).selectinload(Submission.submitter),
            selectinload(Case.assignee),
            selectinload(Case.creator),
            selectinload(Case.notes).selectinload(CaseNote.author),
            selectinload(Case.history),
        )
        .where(Case.id == case_id)
    )
    return result.scalar_one_or_none()


def _record_history(db, case_id: uuid.UUID, changed_by: uuid.UUID, field: str, old: str | None, new: str):
    history = CaseHistory(
        case_id=case_id,
        changed_by=changed_by,
        field_changed=field,
        old_value=old,
        new_value=new,
    )
    db.add(history)


# ── LIST CASES ────────────────────────────────────────────────
@router.get("/", response_model=PaginatedCases, summary="List cases with filtering")
async def list_cases(
    current_user: AnyAuthUser,
    db: DBSession,
    pagination: Pagination,
    status: str | None = Query(default=None, description="Filter by status"),
    classification: str | None = Query(default=None, description="Filter by classification"),
    content_type: str | None = Query(default=None, description="Filter by content type"),
    assigned_to_me: bool = Query(default=False),
    search: str | None = Query(default=None, description="Search by case number"),
):
    """List all cases (paginated). Viewers see all; analysts/admins see all."""
    query = (
        select(Case)
        .join(Submission, Case.submission_id == Submission.id)
        .outerjoin(User, Case.assigned_to == User.id)
    )

    if status:
        query = query.where(Case.status == status)
    if assigned_to_me:
        query = query.where(Case.assigned_to == current_user.id)
    if search:
        query = query.where(Submission.case_number.ilike(f"%{search}%"))
    if content_type:
        query = query.where(Submission.content_type == content_type)

    # Count
    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = (
        query
        .options(
            selectinload(Case.submission).selectinload(Submission.analysis),
            selectinload(Case.assignee),
        )
        .order_by(Case.updated_at.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    )

    # Classification filter (join with analysis)
    if classification:
        from app.models.submission import Analysis
        query = query.join(Analysis, Analysis.submission_id == Case.submission_id).where(
            Analysis.classification == classification
        )

    result = await db.execute(query)
    cases = result.scalars().all()

    items = []
    for c in cases:
        sub = c.submission
        analysis = sub.analysis if sub else None
        items.append(CaseSummaryResponse(
            id=c.id,
            status=c.status,
            priority=c.priority,
            is_escalated=c.is_escalated,
            created_at=c.created_at,
            updated_at=c.updated_at,
            case_number=sub.case_number if sub else "",
            content_type=sub.content_type if sub else "",
            risk_score=analysis.risk_score if analysis else None,
            classification=analysis.classification if analysis else None,
            assignee_name=c.assignee.full_name if c.assignee else None,
        ))

    return PaginatedCases(
        items=items,
        total_count=total,
        page=pagination.page,
        page_size=pagination.page_size,
        total_pages=max(1, -(-total // pagination.page_size)),
    )


# ── GET CASE DETAIL ────────────────────────────────────────────
@router.get("/{case_id}", response_model=CaseDetailResponse, summary="Get full case details")
async def get_case(
    case_id: uuid.UUID,
    current_user: AnyAuthUser,
    db: DBSession,
):
    case = await _get_case_with_details(case_id, db)
    if not case:
        raise NotFoundError("Case")

    sub = case.submission
    analysis = sub.analysis if sub else None

    preview = None
    if sub and sub.content_text:
        preview = sub.content_text[:500] + "..." if len(sub.content_text) > 500 else sub.content_text

    return CaseDetailResponse(
        id=case.id,
        status=case.status,
        priority=case.priority,
        is_escalated=case.is_escalated,
        escalation_reason=case.escalation_reason,
        resolution_summary=case.resolution_summary,
        resolved_at=case.resolved_at,
        created_at=case.created_at,
        updated_at=case.updated_at,
        submission_id=sub.id if sub else case.submission_id,
        case_number=sub.case_number if sub else "",
        content_type=sub.content_type if sub else "",
        content_text_preview=preview,
        content_url=sub.content_url if sub else None,
        file_name=sub.file_name if sub else None,
        source_url=sub.source_url if sub else None,
        analyst_notes=sub.analyst_notes if sub else None,
        language=sub.language if sub else None,
        submitted_at=sub.created_at if sub else case.created_at,
        submitter_name=sub.submitter.full_name if (sub and sub.submitter) else None,
        risk_score=analysis.risk_score if analysis else None,
        classification=analysis.classification if analysis else None,
        confidence=float(analysis.confidence) if (analysis and analysis.confidence) else None,
        explanation_fr=analysis.explanation_fr if analysis else None,
        explanation_en=analysis.explanation_en if analysis else None,
        engine_used=analysis.engine_used if analysis else None,
        processing_time_ms=analysis.processing_time_ms if analysis else None,
        analyzed_at=analysis.analyzed_at if analysis else None,
        assignee_id=case.assigned_to,
        assignee_name=case.assignee.full_name if case.assignee else None,
        creator_name=case.creator.full_name if case.creator else None,
        notes=[CaseNoteResponse.from_note(n) for n in case.notes],
        history=[
            CaseHistoryResponse(
                id=h.id,
                field_changed=h.field_changed,
                old_value=h.old_value,
                new_value=h.new_value,
                changed_at=h.changed_at,
                changed_by_name=h.changer.full_name if h.changer else None,
            )
            for h in case.history
        ],
    )


# ── UPDATE CASE STATUS ─────────────────────────────────────────
@router.patch("/{case_id}/status", summary="Update case status")
async def update_case_status(
    case_id: uuid.UUID,
    payload: CaseStatusUpdate,
    current_user: AnalystUser,
    db: DBSession,
):
    """Move a case through the status state machine: open → in_review → resolved → archived."""
    result = await db.execute(select(Case).where(Case.id == case_id))
    case = result.scalar_one_or_none()
    if not case:
        raise NotFoundError("Case")

    # Validate state transition
    valid_transitions = {
        "open": ["in_review"],
        "in_review": ["open", "resolved"],
        "resolved": ["archived"],
        "archived": [],
    }
    allowed = valid_transitions.get(case.status, [])
    if payload.status not in allowed:
        raise ValidationError(
            f"Cannot transition from '{case.status}' to '{payload.status}'. "
            f"Allowed: {allowed}"
        )

    old_status = case.status
    case.status = payload.status

    if payload.status == "resolved":
        case.resolution_summary = payload.resolution_summary
        case.resolved_at = datetime.now(UTC)

    _record_history(db, case_id, current_user.id, "status", old_status, payload.status)

    audit = AuditLog(
        user_id=current_user.id,
        action=AuditAction.CASE_UPDATED,
        resource_type="case",
        resource_id=case_id,
        details={"field": "status", "from": old_status, "to": payload.status},
    )
    db.add(audit)

    logger.info(f"Case {case_id}: status {old_status} → {payload.status} by {current_user.email}")
    return {"message": f"Case status updated to '{payload.status}'", "case_id": str(case_id)}


# ── ASSIGN CASE ────────────────────────────────────────────────
@router.patch("/{case_id}/assign", summary="Assign case to an analyst")
async def assign_case(
    case_id: uuid.UUID,
    payload: CaseAssignUpdate,
    current_user: AnalystUser,
    db: DBSession,
):
    result = await db.execute(select(Case).where(Case.id == case_id))
    case = result.scalar_one_or_none()
    if not case:
        raise NotFoundError("Case")

    old_assignee = str(case.assigned_to) if case.assigned_to else "unassigned"

    if payload.analyst_id:
        # Verify analyst exists
        analyst = await db.get(User, payload.analyst_id)
        if not analyst:
            raise NotFoundError("Analyst")
        case.assigned_to = payload.analyst_id
        new_assignee = analyst.full_name
    else:
        case.assigned_to = None
        new_assignee = "unassigned"

    _record_history(db, case_id, current_user.id, "assigned_to", old_assignee, new_assignee)

    audit = AuditLog(
        user_id=current_user.id,
        action=AuditAction.CASE_ASSIGNED,
        resource_type="case",
        resource_id=case_id,
        details={"assigned_to": str(payload.analyst_id)},
    )
    db.add(audit)
    return {"message": f"Case assigned to {new_assignee}"}


# ── ADD NOTE ───────────────────────────────────────────────────
@router.post("/{case_id}/notes", response_model=CaseNoteResponse, summary="Add note to case")
async def add_note(
    case_id: uuid.UUID,
    payload: CaseNoteCreate,
    current_user: AnalystUser,
    db: DBSession,
):
    result = await db.execute(select(Case).where(Case.id == case_id))
    if not result.scalar_one_or_none():
        raise NotFoundError("Case")

    note = CaseNote(case_id=case_id, author_id=current_user.id, content=payload.content)
    db.add(note)

    audit = AuditLog(
        user_id=current_user.id,
        action=AuditAction.CASE_NOTE_ADDED,
        resource_type="case",
        resource_id=case_id,
    )
    db.add(audit)
    await db.commit()

    # Reload with author
    await db.refresh(note)
    return CaseNoteResponse(
        id=note.id,
        case_id=note.case_id,
        content=note.content,
        created_at=note.created_at,
        author_full_name=current_user.full_name,
        author_email=current_user.email,
    )


# ── LIST NOTES ─────────────────────────────────────────────────
@router.get("/{case_id}/notes", response_model=list[CaseNoteResponse], summary="List case notes")
async def list_notes(
    case_id: uuid.UUID,
    current_user: AnyAuthUser,
    db: DBSession,
):
    result = await db.execute(
        select(CaseNote)
        .options(selectinload(CaseNote.author))
        .where(CaseNote.case_id == case_id)
        .order_by(CaseNote.created_at.asc())
    )
    notes = result.scalars().all()
    return [CaseNoteResponse.from_note(n) for n in notes]


# ── ESCALATE CASE ──────────────────────────────────────────────
@router.post("/{case_id}/escalate", summary="Escalate case to Admin")
async def escalate_case(
    case_id: uuid.UUID,
    payload: CaseEscalateRequest,
    current_user: AnalystUser,
    db: DBSession,
):
    result = await db.execute(select(Case).where(Case.id == case_id))
    case = result.scalar_one_or_none()
    if not case:
        raise NotFoundError("Case")

    if case.is_escalated:
        raise ValidationError("Case is already escalated")

    case.is_escalated = True
    case.escalation_reason = payload.reason

    # Create alert for all admins
    admin_result = await db.execute(
        select(User)
        .join(User.role)
        .where(User.role.has(name="admin"), User.is_active == True)  # noqa: E712
    )
    admins = admin_result.scalars().all()

    sub_result = await db.execute(select(Submission).where(Submission.id == case.submission_id))
    sub = sub_result.scalar_one_or_none()
    case_number = sub.case_number if sub else str(case_id)

    for admin in admins:
        alert = Alert(
            case_id=case_id,
            recipient_id=admin.id,
            alert_type="CASE_ESCALATED",
            channel=AlertChannel.WEBSOCKET.value,
            message=f"Case {case_number} escalated by {current_user.full_name}: {payload.reason[:100]}",
        )
        db.add(alert)

    _record_history(db, case_id, current_user.id, "is_escalated", "false", "true")
    audit = AuditLog(
        user_id=current_user.id,
        action=AuditAction.CASE_ESCALATED,
        resource_type="case",
        resource_id=case_id,
        details={"reason": payload.reason},
    )
    db.add(audit)

    return {"message": "Case escalated to administrators successfully"}


# ── EXPORT CASES ───────────────────────────────────────────────
@router.get("/export/csv", summary="Export cases to CSV (Admin only)")
async def export_cases(
    current_user: AdminUser,
    db: DBSession,
    status: str | None = Query(default=None),
):
    """Export all cases as a CSV file."""
    import csv

    query = (
        select(Case)
        .options(
            selectinload(Case.submission).selectinload(Submission.analysis),
            selectinload(Case.assignee),
        )
        .order_by(Case.created_at.desc())
    )
    if status:
        query = query.where(Case.status == status)

    result = await db.execute(query)
    cases = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Case ID", "Case Number", "Status", "Priority", "Classification",
        "Risk Score", "Content Type", "Assigned To", "Escalated",
        "Created At", "Updated At",
    ])

    for c in cases:
        sub = c.submission
        analysis = sub.analysis if sub else None
        writer.writerow([
            str(c.id),
            sub.case_number if sub else "",
            c.status,
            c.priority,
            analysis.classification if analysis else "",
            analysis.risk_score if analysis else "",
            sub.content_type if sub else "",
            c.assignee.full_name if c.assignee else "",
            "Yes" if c.is_escalated else "No",
            c.created_at.isoformat(),
            c.updated_at.isoformat(),
        ])

    output.seek(0)
    filename = f"vigil_ai_cases_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),  # UTF-8 BOM for Excel
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
