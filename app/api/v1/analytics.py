"""
VIGIL-AI Cameroun — Analytics Router
GET /api/v1/analytics/overview
GET /api/v1/analytics/timeline
GET /api/v1/analytics/by-type
"""
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import case, func, select

from app.api.deps import AnyAuthUser, DBSession
from app.models.case import Case
from app.models.submission import Analysis, Submission
from app.schemas.analytics import (
    ContentTypeBreakdown,
    DashboardOverview,
    RiskDistribution,
    RiskScoreBucket,
    TimelinePoint,
)

router = APIRouter(prefix="/analytics", tags=["Analytics"])
logger = logging.getLogger(__name__)


@router.get("/overview", summary="Dashboard KPI metrics")
async def get_overview(current_user: AnyAuthUser, db: DBSession):
    """Return the main dashboard KPI metrics."""
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Total submissions
    total_subs = (await db.execute(select(func.count(Submission.id)))).scalar() or 0
    subs_today = (
        await db.execute(
            select(func.count(Submission.id)).where(Submission.created_at >= today_start)
        )
    ).scalar() or 0

    # Cases by status
    case_counts = (
        await db.execute(
            select(Case.status, func.count(Case.id))
            .group_by(Case.status)
        )
    ).all()
    status_map = {row[0]: row[1] for row in case_counts}

    # Cases today
    cases_today = (
        await db.execute(
            select(func.count(Case.id)).where(Case.created_at >= today_start)
        )
    ).scalar() or 0

    # Classification counts
    classification_counts = (
        await db.execute(
            select(Analysis.classification, func.count(Analysis.id))
            .where(Analysis.classification.isnot(None))
            .group_by(Analysis.classification)
        )
    ).all()
    cls_map = {row[0]: row[1] for row in classification_counts}

    malicious_today = (
        await db.execute(
            select(func.count(Analysis.id))
            .join(Submission, Analysis.submission_id == Submission.id)
            .where(
                Analysis.classification == "malicious",
                Submission.created_at >= today_start,
            )
        )
    ).scalar() or 0

    total_cases = sum(status_map.values())

    overview = DashboardOverview(
        total_submissions=total_subs,
        total_cases=total_cases,
        open_cases=status_map.get("open", 0),
        in_review_cases=status_map.get("in_review", 0),
        resolved_cases=status_map.get("resolved", 0),
        malicious_cases=cls_map.get("malicious", 0),
        suspicious_cases=cls_map.get("suspicious", 0),
        safe_cases=cls_map.get("safe", 0),
        submissions_today=subs_today,
        cases_today=cases_today,
        malicious_today=malicious_today,
    )
    return overview


@router.get("/timeline", summary="Threat timeline chart data")
async def get_timeline(
    current_user: AnyAuthUser,
    db: DBSession,
    days: int = Query(default=30, ge=7, le=365),
):
    """Return daily threat counts for the last N days."""
    since = datetime.now(UTC) - timedelta(days=days)

    rows = (
        await db.execute(
            select(
                func.date(Submission.created_at).label("date"),
                func.count(Submission.id).label("total"),
                func.sum(
                    case((Analysis.classification == "malicious", 1), else_=0)
                ).label("malicious"),
                func.sum(
                    case((Analysis.classification == "suspicious", 1), else_=0)
                ).label("suspicious"),
                func.sum(
                    case((Analysis.classification == "safe", 1), else_=0)
                ).label("safe"),
            )
            .outerjoin(Analysis, Analysis.submission_id == Submission.id)
            .where(Submission.created_at >= since)
            .group_by(func.date(Submission.created_at))
            .order_by(func.date(Submission.created_at).asc())
        )
    ).all()

    return [
        TimelinePoint(
            date=str(row.date),
            total=row.total or 0,
            malicious=row.malicious or 0,
            suspicious=row.suspicious or 0,
            safe=row.safe or 0,
        )
        for row in rows
    ]


@router.get("/by-type", summary="Breakdown of cases by content type")
async def get_by_type(current_user: AnyAuthUser, db: DBSession):
    """Return case counts broken down by content type."""
    rows = (
        await db.execute(
            select(Submission.content_type, func.count(Submission.id))
            .group_by(Submission.content_type)
        )
    ).all()

    type_map = {row[0]: row[1] for row in rows}
    total = max(1, sum(type_map.values()))

    text = type_map.get("text", 0)
    image = type_map.get("image", 0)
    video = type_map.get("video", 0)
    audio = type_map.get("audio", 0)

    return ContentTypeBreakdown(
        text_count=text,
        image_count=image,
        video_count=video,
        audio_count=audio,
        text_pct=round(text / total * 100, 1),
        image_pct=round(image / total * 100, 1),
        video_pct=round(video / total * 100, 1),
        audio_pct=round(audio / total * 100, 1),
    )


@router.get("/risk-distribution", summary="Risk score histogram")
async def get_risk_distribution(current_user: AnyAuthUser, db: DBSession):
    """Return risk score distribution as histogram buckets."""
    rows = (
        await db.execute(
            select(Analysis.risk_score)
            .where(Analysis.risk_score.isnot(None))
        )
    ).scalars().all()

    if not rows:
        return RiskDistribution(buckets=[], average_score=None, median_score=None)

    # Build 10-point buckets
    buckets: dict[str, int] = {}
    for i in range(0, 100, 10):
        buckets[f"{i}-{i+9}"] = 0

    for score in rows:
        bucket_start = (score // 10) * 10
        key = f"{bucket_start}-{bucket_start + 9}"
        if key in buckets:
            buckets[key] += 1

    avg = sum(rows) / len(rows)
    sorted_rows = sorted(rows)
    mid = len(sorted_rows) // 2
    median = sorted_rows[mid] if len(sorted_rows) % 2 == 1 else (sorted_rows[mid - 1] + sorted_rows[mid]) / 2

    return RiskDistribution(
        buckets=[RiskScoreBucket(bucket=k, count=v) for k, v in buckets.items()],
        average_score=round(avg, 1),
        median_score=float(median),
    )
