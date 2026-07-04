"""
VIGIL-AI Cameroun — Celery Tasks
All background jobs: AI analysis, alerts, reports, cleanup
"""
import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

import redis as redis_sync

from app.config import settings
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def run_async(coro):
    """Run an async coroutine from a sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        from app.database import engine
        loop.run_until_complete(engine.dispose())
        loop.close()


# ── MAIN ANALYSIS TASK ────────────────────────────────────────
@celery_app.task(
    bind=True,
    name="app.workers.tasks.run_analysis",
    queue="analysis",
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def run_analysis(self, submission_id: str):
    """
    Main AI analysis task.
    Called when a new submission is created.
    Runs the appropriate detector and saves results to DB.
    """
    logger.info(f"Starting analysis for submission {submission_id}")
    return run_async(_run_analysis_async(submission_id))


async def _run_analysis_async(submission_id: str):
    """Async implementation of the analysis task."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.ai.engine import detection_engine
    from app.database import AsyncSessionLocal
    from app.models.audit_log import AuditAction, AuditLog
    from app.models.case import Alert, Case, CasePriority
    from app.models.submission import Analysis, Submission, SubmissionStatus

    sub_uuid = uuid.UUID(submission_id)

    async with AsyncSessionLocal() as db:
        try:
            # Fetch submission
            result = await db.execute(
                select(Submission)
                .options(selectinload(Submission.analysis))
                .where(Submission.id == sub_uuid)
            )
            submission = result.scalar_one_or_none()

            if not submission:
                logger.error(f"Submission {submission_id} not found")
                return

            # Mark as analyzing
            submission.status = SubmissionStatus.ANALYZING.value
            await db.commit()

            # Run AI detection
            logger.info(f"Running {submission.content_type} detection for {submission.case_number}")
            detection = await detection_engine.analyze(
                content_type=submission.content_type,
                content_text=submission.content_text,
                file_path=submission.file_path,
                content_url=submission.content_url,
                language=submission.language,
            )

            # Determine priority from score
            if detection.risk_score >= 70:
                priority = CasePriority.HIGH.value
            elif detection.risk_score >= 50:
                priority = CasePriority.MEDIUM.value
            else:
                priority = CasePriority.LOW.value

            # Update or create analysis record
            if submission.analysis:
                analysis = submission.analysis
            else:
                analysis = Analysis(submission_id=sub_uuid)
                db.add(analysis)

            analysis.risk_score = detection.risk_score
            analysis.classification = detection.classification
            analysis.confidence = float(detection.confidence)
            analysis.explanation_fr = detection.explanation_fr
            analysis.explanation_en = detection.explanation_en
            analysis.engine_used = detection.engine_used
            analysis.processing_time_ms = detection.processing_time_ms
            analysis.raw_api_response = detection.raw_response
            analysis.analyzed_at = datetime.now(UTC)
            analysis.retry_count = 0

            # Mark submission as complete
            submission.status = SubmissionStatus.COMPLETE.value
            await db.commit()

            logger.info(
                f"Analysis complete: {submission.case_number} → "
                f"score={detection.risk_score} ({detection.classification})"
            )

            # Auto-create case if score >= threshold
            if detection.risk_score >= settings.AUTO_CREATE_CASE_MIN_SCORE:
                await _create_case(
                    db, submission, analysis, submission.submitted_by, priority
                )

            # Audit log
            audit = AuditLog(
                user_id=submission.submitted_by,
                action=AuditAction.ANALYSIS_COMPLETED,
                resource_type="submission",
                resource_id=sub_uuid,
                details={
                    "case_number": submission.case_number,
                    "risk_score": detection.risk_score,
                    "classification": detection.classification,
                    "engine": detection.engine_used,
                },
            )
            db.add(audit)
            await db.commit()

            # Notify via Redis pub/sub (WebSocket handler picks this up)
            _publish_analysis_complete(submission_id, {
                "type": "analysis_complete",
                "submission_id": submission_id,
                "case_number": submission.case_number,
                "risk_score": detection.risk_score,
                "classification": detection.classification,
                "user_id": str(submission.submitted_by),
            })

        except Exception as e:
            logger.error(f"Analysis failed for {submission_id}: {e}", exc_info=True)
            # Mark as failed
            async with AsyncSessionLocal() as db2:
                result = await db2.execute(select(Submission).where(Submission.id == sub_uuid))
                sub = result.scalar_one_or_none()
                if sub:
                    sub.status = SubmissionStatus.FAILED.value
                    # Save error to analysis record
                    if sub.analysis:
                        sub.analysis.error_message = str(e)
                        sub.analysis.retry_count += 1
                    await db2.commit()
            raise


async def _create_case(db, submission, analysis, created_by_id, priority: str):
    """Auto-create a case for suspicious/malicious content."""
    from app.models.audit_log import AuditAction, AuditLog
    from app.models.case import Alert, AlertChannel, Case

    # Check if case already exists
    from sqlalchemy import select
    existing = await db.execute(
        select(Case).where(Case.submission_id == submission.id)
    )
    if existing.scalar_one_or_none():
        return  # Case already exists

    case = Case(
        submission_id=submission.id,
        created_by=created_by_id,
        assigned_to=created_by_id,  # Auto-assign to submitter
        priority=priority,
        status="open",
    )
    db.add(case)
    await db.flush()  # Get case.id

    # Create alert notification
    alert = Alert(
        case_id=case.id,
        recipient_id=created_by_id,
        alert_type="CASE_CREATED",
        channel=AlertChannel.WEBSOCKET.value,
        message=(
            f"Nouveau cas créé: {submission.case_number} | "
            f"Score de risque: {analysis.risk_score}/100 ({analysis.classification})"
        ),
    )
    db.add(alert)

    # Audit
    audit_entry = AuditLog(
        user_id=created_by_id,
        action=AuditAction.CASE_CREATED,
        resource_type="case",
        resource_id=case.id,
        details={
            "case_number": submission.case_number,
            "risk_score": analysis.risk_score,
            "classification": analysis.classification,
            "auto_created": True,
        },
    )
    db.add(audit_entry)

    logger.info(
        f"Case auto-created for {submission.case_number} "
        f"(score={analysis.risk_score}, priority={priority})"
    )

    # Send email alert for malicious cases
    if analysis.classification == "malicious":
        send_alert_email.delay(
            str(created_by_id),
            submission.case_number,
            analysis.risk_score,
        )


def _publish_analysis_complete(submission_id: str, data: dict):
    """Publish analysis completion event to Redis pub/sub for WebSocket delivery."""
    try:
        r = redis_sync.from_url(settings.REDIS_URL)
        r.publish("analysis_events", json.dumps(data))
        r.close()
    except Exception as e:
        logger.warning(f"Redis pub/sub publish failed: {e}")


# ── EMAIL ALERT TASK ──────────────────────────────────────────
@celery_app.task(
    name="app.workers.tasks.send_alert_email",
    queue="alerts",
    max_retries=3,
)
def send_alert_email(user_id: str, case_number: str, risk_score: int):
    """Send email alert for high-risk cases."""
    return run_async(_send_alert_email_async(user_id, case_number, risk_score))


async def _send_alert_email_async(user_id: str, case_number: str, risk_score: int):
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.user import User

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(User.id == uuid.UUID(user_id))
        )
        user = result.scalar_one_or_none()
        if not user:
            return

    if settings.EMAIL_ENABLED:
        # Send via SMTP
        import aiosmtplib
        from email.mime.text import MIMEText

        msg = MIMEText(
            f"""
            Bonjour {user.full_name},

            Un nouveau cas MALICIOUS a été créé sur VIGIL-AI Cameroun:

            Numéro de cas: {case_number}
            Score de risque: {risk_score}/100
            Classification: MALICIOUS

            Connectez-vous à la plateforme pour investiguer ce cas immédiatement.

            ---
            VIGIL-AI Cameroun — Surveillance IA Nationale
            """,
            "plain",
            "utf-8",
        )
        msg["From"] = settings.SMTP_FROM
        msg["To"] = user.email
        msg["Subject"] = f"[VIGILAI ALERTE] Cas malveillant détecté: {case_number}"

        try:
            await aiosmtplib.send(
                msg,
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USER or None,
                password=settings.SMTP_PASSWORD or None,
                use_tls=settings.SMTP_USE_TLS,
            )
            logger.info(f"Alert email sent to {user.email} for case {case_number}")
        except Exception as e:
            logger.error(f"Failed to send alert email: {e}")
    else:
        # Print to console (development mode)
        logger.warning(
            f"\n{'='*60}\n"
            f"📧 EMAIL ALERT (console mode — set EMAIL_ENABLED=True for real emails)\n"
            f"To: {user.email}\n"
            f"Subject: [VIGILAI ALERTE] Cas malveillant: {case_number}\n"
            f"Body: Case {case_number} — Risk Score: {risk_score}/100 — MALICIOUS\n"
            f"{'='*60}"
        )


# ── MONTHLY REPORT TASK ───────────────────────────────────────
@celery_app.task(
    name="app.workers.tasks.generate_monthly_report",
    queue="default",
)
def generate_monthly_report():
    """Generate the monthly threat transparency report."""
    logger.info("Monthly report generation started")
    # TODO: Generate PDF report — implement in Phase 2
    logger.info("Monthly report generation complete (stub)")


# ── CLEANUP TASK ──────────────────────────────────────────────
@celery_app.task(
    name="app.workers.tasks.cleanup_video_frames",
    queue="default",
)
def cleanup_video_frames():
    """Clean up temporary video frame files."""
    import shutil
    upload_dir = Path(settings.UPLOAD_DIR)
    cleaned = 0
    for frame_dir in upload_dir.rglob("frames"):
        if frame_dir.is_dir():
            shutil.rmtree(frame_dir, ignore_errors=True)
            cleaned += 1
    logger.info(f"Cleaned up {cleaned} frame directories")
