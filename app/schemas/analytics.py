"""
VIGIL-AI Cameroun — Analytics Schemas
"""
from pydantic import BaseModel


class DashboardOverview(BaseModel):
    total_submissions: int
    total_cases: int
    open_cases: int
    in_review_cases: int
    resolved_cases: int
    malicious_cases: int
    suspicious_cases: int
    safe_cases: int
    # Today's stats
    submissions_today: int
    cases_today: int
    malicious_today: int


class TimelinePoint(BaseModel):
    date: str          # ISO date string YYYY-MM-DD
    total: int
    malicious: int
    suspicious: int
    safe: int


class ContentTypeBreakdown(BaseModel):
    text_count: int
    image_count: int
    video_count: int
    audio_count: int
    text_pct: float
    image_pct: float
    video_pct: float
    audio_pct: float


class RiskScoreBucket(BaseModel):
    bucket: str        # e.g. "0-9", "10-19", ...
    count: int


class RiskDistribution(BaseModel):
    buckets: list[RiskScoreBucket]
    average_score: float | None
    median_score: float | None


class AnalyticsOverviewResponse(BaseModel):
    overview: DashboardOverview
    timeline: list[TimelinePoint]
    content_breakdown: ContentTypeBreakdown
    risk_distribution: RiskDistribution
