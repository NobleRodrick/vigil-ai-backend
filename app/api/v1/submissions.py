"""
VIGIL-AI Cameroun — Submissions Router
POST /api/v1/submissions/text
POST /api/v1/submissions/image
POST /api/v1/submissions/video
POST /api/v1/submissions/audio
GET  /api/v1/submissions
GET  /api/v1/submissions/{id}
DELETE /api/v1/submissions/{id}
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, File, Form, Query, UploadFile
from sqlalchemy import Select, func, select
from sqlalchemy.orm import selectinload

from app.api.deps import AdminUser, AnalystUser, AnyAuthUser, DBSession, Pagination
from app.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.models.audit_log import AuditAction, AuditLog
from app.models.submission import Analysis, Submission, SubmissionStatus
from app.models.user import User
from app.schemas.submission import (
    PaginatedSubmissions,
    SubmissionDetailResponse,
    SubmissionQueuedResponse,
    SubmissionResponse,
    TextSubmissionCreate,
    VideoSubmissionCreate,
)
from app.services.storage_service import storage_service

router = APIRouter(prefix="/submissions", tags=["Submissions"])
logger = logging.getLogger(__name__)


# ── Shared Helpers ─────────────────────────────────────────────
async def _generate_case_number(db) -> str:
    """Generate sequential case number: VIGIL-2026-00001."""
    year = datetime.now(timezone.utc).year
    result = await db.execute(
        select(func.count(Submission.id)).where(
            func.extract("year", Submission.created_at) == year
        )
    )
    count = (result.scalar() or 0) + 1
    return f"VIGIL-{year}-{count:05d}"


async def _run_analysis_inline(submission_id: str):
    """Fallback: run the analysis inside the API process when the Celery
    broker is unreachable, so submissions never sit in 'queued' forever."""
    try:
        from app.workers.tasks import _run_analysis_async
        await _run_analysis_async(submission_id)
    except Exception as e:
        logger.error(f"Inline analysis fallback failed for {submission_id}: {e}")


def _enqueue_analysis(submission_id: str, background_tasks: BackgroundTasks):
    """Dispatch analysis to Celery; degrade to an in-process background task
    if the broker is unavailable."""
    try:
        from app.workers.tasks import run_analysis
        run_analysis.delay(submission_id)
        logger.info(f"Analysis task enqueued for {submission_id}")
    except Exception as e:
        logger.warning(f"Celery unavailable ({e}) — running analysis in-process")
        background_tasks.add_task(_run_analysis_inline, submission_id)


def _validate_media_url(url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValidationError("content_url must be a valid HTTP(S) URL")
    return url


async def _mirror_to_cloudinary(file_bytes: bytes, content_type: str, case_number: str) -> str | None:
    """Best-effort mirror of an uploaded file to Cloudinary (if configured)."""
    from starlette.concurrency import run_in_threadpool
    from app.services.cloudinary_service import cloudinary_service

    if not cloudinary_service.enabled:
        return None
    return await run_in_threadpool(
        cloudinary_service.upload, file_bytes, content_type, case_number
    )


# ── TEXT SUBMISSION ────────────────────────────────────────────
@router.post(
    "/text",
    response_model=SubmissionQueuedResponse,
    status_code=202,
    summary="Submit text content for AI analysis",
)
async def submit_text(
    payload: TextSubmissionCreate,
    current_user: AnalystUser,
    db: DBSession,
    background_tasks: BackgroundTasks,
):
    """
    Submit text (French, English, or Cameroonian Pidgin) for AI-detection analysis.
    Analysis runs asynchronously — check status via GET /submissions/{id}.
    """
    case_number = await _generate_case_number(db)

    submission = Submission(
        case_number=case_number,
        submitted_by=current_user.id,
        content_type="text",
        content_text=payload.content_text,
        language=payload.language or "auto",
        source_url=payload.source_url,
        analyst_notes=payload.analyst_notes,
        status=SubmissionStatus.QUEUED.value,
    )
    db.add(submission)
    await db.flush()

    # Pre-create analysis record
    analysis = Analysis(submission_id=submission.id)
    db.add(analysis)

    audit = AuditLog(
        user_id=current_user.id,
        action=AuditAction.SUBMISSION_CREATED,
        resource_type="submission",
        resource_id=submission.id,
        details={"case_number": case_number, "type": "text"},
    )
    db.add(audit)
    await db.commit()

    # Enqueue analysis
    _enqueue_analysis(str(submission.id), background_tasks)

    logger.info(f"Text submission created: {case_number} by {current_user.email}")
    return SubmissionQueuedResponse(
        case_number=case_number,
        submission_id=submission.id,
        message=f"Case {case_number} created. Analysis queued — you'll be notified when complete.",
    )


# ── IMAGE SUBMISSION ───────────────────────────────────────────
@router.post(
    "/image",
    response_model=SubmissionQueuedResponse,
    status_code=202,
    summary="Submit an image (URL or optional file upload) for deepfake detection",
)
async def submit_image(
    current_user: AnalystUser,
    db: DBSession,
    background_tasks: BackgroundTasks,
    file: UploadFile | None = File(default=None, description="Image file (JPG, PNG, WebP) — optional"),
    content_url: str | None = Form(default=None, description="Direct URL of the image to analyze"),
    source_url: str | None = Form(default=None),
    analyst_notes: str | None = Form(default=None),
):
    """
    Submit an image for AI-based deepfake and manipulation detection.
    Provide EITHER a direct image URL (default workflow) OR upload a file.
    Uploaded files are mirrored to Cloudinary when configured.
    """
    if file is None and not content_url:
        raise ValidationError("Provide an image URL (content_url) or upload a file")

    case_number = await _generate_case_number(db)
    file_path = file_name = mime_type = None
    file_size = None
    media_url = None

    if file is not None:
        file_bytes = await file.read()
        # Save to storage (validation happens inside)
        file_path, safe_filename, file_size = await storage_service.save_file(
            file_bytes=file_bytes,
            original_filename=file.filename or "image",
            content_type="image",
            expected_mime_types=settings.ALLOWED_IMAGE_TYPE_LIST,
        )
        file_name = file.filename
        mime_type = file.content_type
        # Optional durable mirror (also lets the worker re-fetch the media
        # if the local disk is ephemeral)
        media_url = await _mirror_to_cloudinary(file_bytes, "image", case_number)
    else:
        media_url = _validate_media_url(content_url)

    submission = Submission(
        case_number=case_number,
        submitted_by=current_user.id,
        content_type="image",
        file_path=file_path,
        file_name=file_name,
        file_size_bytes=file_size,
        mime_type=mime_type,
        content_url=media_url,
        source_url=source_url,
        analyst_notes=analyst_notes,
        status=SubmissionStatus.QUEUED.value,
    )
    db.add(submission)
    await db.flush()

    # Pre-create analysis record
    analysis = Analysis(submission_id=submission.id)
    db.add(analysis)
    audit = AuditLog(
        user_id=current_user.id,
        action=AuditAction.SUBMISSION_CREATED,
        resource_type="submission",
        resource_id=submission.id,
        details={
            "case_number": case_number,
            "type": "image",
            "filename": file_name,
            "content_url": content_url,
        },
    )
    db.add(audit)
    await db.commit()

    _enqueue_analysis(str(submission.id), background_tasks)

    return SubmissionQueuedResponse(
        case_number=case_number,
        submission_id=submission.id,
        message=f"Image submitted as {case_number}. Deepfake analysis queued.",
    )


# ── VIDEO URL SUBMISSION ───────────────────────────────────────
@router.post(
    "/video",
    response_model=SubmissionQueuedResponse,
    status_code=202,
    summary="Submit a video URL for deepfake analysis",
)
async def submit_video(
    payload: VideoSubmissionCreate,
    current_user: AnalystUser,
    db: DBSession,
    background_tasks: BackgroundTasks,
):
    """Submit a video URL (YouTube, Facebook, TikTok… or a direct MP4 link) for deepfake analysis."""
    case_number = await _generate_case_number(db)
    submission = Submission(
        case_number=case_number,
        submitted_by=current_user.id,
        content_type="video",
        content_url=payload.content_url,
        source_url=payload.source_url,
        analyst_notes=payload.analyst_notes,
        status=SubmissionStatus.QUEUED.value,
    )
    db.add(submission)

    await db.flush()
    analysis = Analysis(submission_id=submission.id)
    db.add(analysis)
    audit = AuditLog(
        user_id=current_user.id,
        action=AuditAction.SUBMISSION_CREATED,
        resource_type="submission",
        resource_id=submission.id,
        details={"case_number": case_number, "type": "video", "url": payload.content_url},
    )
    db.add(audit)
    await db.commit()

    _enqueue_analysis(str(submission.id), background_tasks)

    return SubmissionQueuedResponse(
        case_number=case_number,
        submission_id=submission.id,
        message=f"Video URL submitted as {case_number}. Analysis queued.",
    )


# ── AUDIO SUBMISSION ───────────────────────────────────────────
@router.post(
    "/audio",
    response_model=SubmissionQueuedResponse,
    status_code=202,
    summary="Submit audio (URL or optional file upload) for voice clone detection",
)
async def submit_audio(
    current_user: AnalystUser,
    db: DBSession,
    background_tasks: BackgroundTasks,
    file: UploadFile | None = File(default=None, description="Audio file (MP3, WAV, OGG) — optional"),
    content_url: str | None = Form(default=None, description="Direct URL of the audio to analyze"),
    source_url: str | None = Form(default=None),
    analyst_notes: str | None = Form(default=None),
):
    """
    Submit audio for voice cloning and synthetic speech detection.
    Provide EITHER a direct audio URL (default workflow) OR upload a file.
    Uploaded files are mirrored to Cloudinary when configured.
    """
    if file is None and not content_url:
        raise ValidationError("Provide an audio URL (content_url) or upload a file")

    case_number = await _generate_case_number(db)
    file_path = file_name = mime_type = None
    file_size = None
    media_url = None

    if file is not None:
        file_bytes = await file.read()
        file_path, safe_filename, file_size = await storage_service.save_file(
            file_bytes=file_bytes,
            original_filename=file.filename or "audio",
            content_type="audio",
            expected_mime_types=settings.ALLOWED_AUDIO_TYPE_LIST,
        )
        file_name = file.filename
        mime_type = file.content_type
        media_url = await _mirror_to_cloudinary(file_bytes, "audio", case_number)
    else:
        media_url = _validate_media_url(content_url)

    submission = Submission(
        case_number=case_number,
        submitted_by=current_user.id,
        content_type="audio",
        file_path=file_path,
        file_name=file_name,
        file_size_bytes=file_size,
        mime_type=mime_type,
        content_url=media_url,
        source_url=source_url,
        analyst_notes=analyst_notes,
        status=SubmissionStatus.QUEUED.value,
    )
    db.add(submission)
    await db.flush()

    analysis = Analysis(submission_id=submission.id)
    db.add(analysis)
    audit = AuditLog(
        user_id=current_user.id,
        action=AuditAction.SUBMISSION_CREATED,
        resource_type="submission",
        resource_id=submission.id,
        details={"case_number": case_number, "type": "audio", "content_url": content_url},
    )
    db.add(audit)
    await db.commit()

    _enqueue_analysis(str(submission.id), background_tasks)

    return SubmissionQueuedResponse(
        case_number=case_number,
        submission_id=submission.id,
        message=f"Audio submitted as {case_number}. Voice analysis queued.",
    )


# ── LIST SUBMISSIONS ───────────────────────────────────────────
@router.get("/", response_model=PaginatedSubmissions, summary="List all submissions")
async def list_submissions(
    current_user: AnyAuthUser,
    db: DBSession,
    pagination: Pagination,
    content_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    my_only: bool = Query(default=False, description="Show only my submissions"),
):
    """List all submissions with optional filtering."""
    query = select(Submission)

    if my_only or current_user.role_name == "viewer":
        query = query.where(Submission.submitted_by == current_user.id)
    if content_type:
        query = query.where(Submission.content_type == content_type)
    if status:
        query = query.where(Submission.status == status)

    # Count
    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # Paginate (eager-load analysis so list rows can show the verdict)
    query = query.options(selectinload(Submission.analysis))
    query = query.order_by(Submission.created_at.desc())
    query = query.offset(pagination.offset).limit(pagination.page_size)
    result = await db.execute(query)
    submissions = result.scalars().all()

    return PaginatedSubmissions(
        items=[SubmissionResponse.from_submission(s) for s in submissions],
        total_count=total,
        page=pagination.page,
        page_size=pagination.page_size,
        total_pages=max(1, -(-total // pagination.page_size)),
    )


# ── GET SINGLE SUBMISSION ──────────────────────────────────────
@router.get(
    "/{submission_id}",
    response_model=SubmissionDetailResponse,
    summary="Get a submission with its analysis result",
)
async def get_submission(
    submission_id: uuid.UUID,
    current_user: AnyAuthUser,
    db: DBSession,
):
    """Get full submission details including the AI analysis result."""
    result = await db.execute(
        select(Submission)
        .options(
            selectinload(Submission.analysis),
            selectinload(Submission.submitter),
        )
        .where(Submission.id == submission_id)
    )
    submission = result.scalar_one_or_none()
    if not submission:
        raise NotFoundError("Submission")

    # Viewers can only see their own
    if current_user.role_name == "viewer" and submission.submitted_by != current_user.id:
        raise ForbiddenError("view this submission")

    preview = None
    if submission.content_text:
        preview = submission.content_text[:500] + "..." if len(submission.content_text) > 500 else submission.content_text

    from app.schemas.submission import AnalysisResponse
    analysis_resp = None
    if submission.analysis:
        analysis_resp = AnalysisResponse.from_analysis(submission.analysis)

    return SubmissionDetailResponse(
        id=submission.id,
        case_number=submission.case_number,
        content_type=submission.content_type,
        status=submission.status,
        language=submission.language,
        source_url=submission.source_url,
        analyst_notes=submission.analyst_notes,
        file_name=submission.file_name,
        file_size_bytes=submission.file_size_bytes,
        content_text_preview=preview,
        content_url=submission.content_url,
        submitted_by_name=submission.submitter.full_name if submission.submitter else None,
        analysis=analysis_resp,
        created_at=submission.created_at,
        updated_at=submission.updated_at,
    )


# ── DELETE SUBMISSION ──────────────────────────────────────────
@router.delete("/{submission_id}", status_code=204, summary="Delete a submission (Admin only)")
async def delete_submission(
    submission_id: uuid.UUID,
    current_user: AdminUser,
    db: DBSession,
):
    """Permanently delete a submission and its associated files."""
    result = await db.execute(select(Submission).where(Submission.id == submission_id))
    submission = result.scalar_one_or_none()
    if not submission:
        raise NotFoundError("Submission")

    # Delete media file if present
    if submission.file_path:
        await storage_service.delete_file(submission.file_path)

    audit = AuditLog(
        user_id=current_user.id,
        action=AuditAction.SUBMISSION_DELETED,
        resource_type="submission",
        resource_id=submission_id,
        details={"case_number": submission.case_number},
    )
    db.add(audit)
    await db.delete(submission)
    logger.info(f"Submission {submission.case_number} deleted by {current_user.email}")
