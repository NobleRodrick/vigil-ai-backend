"""
VIGIL-AI Cameroun — Audit Log Router (Admin)
GET /api/v1/audit-logs          — paginated, filterable audit trail
GET /api/v1/audit-logs/actions  — distinct action names (for filter dropdowns)
"""
import logging

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import AdminUser, DBSession, Pagination
from app.models.audit_log import AuditLog
from app.models.user import User
from app.schemas.audit import AuditLogResponse, PaginatedAuditLogs

router = APIRouter(prefix="/audit-logs", tags=["Audit Log"])
logger = logging.getLogger(__name__)


@router.get("/", response_model=PaginatedAuditLogs, summary="List audit log entries (Admin only)")
async def list_audit_logs(
    current_user: AdminUser,
    db: DBSession,
    pagination: Pagination,
    action: str | None = Query(default=None, description="Filter by exact action, e.g. USER_LOGIN"),
    resource_type: str | None = Query(default=None, description="Filter by resource type"),
    user_search: str | None = Query(default=None, description="Filter by user email or name"),
):
    """Tamper-evident trail of every significant action on the platform."""
    query = select(AuditLog)

    if action:
        query = query.where(AuditLog.action == action)
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
    if user_search:
        query = query.join(User, AuditLog.user_id == User.id).where(
            User.email.ilike(f"%{user_search}%") | User.full_name.ilike(f"%{user_search}%")
        )

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = (
        query.options(selectinload(AuditLog.user))
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    )
    logs = (await db.execute(query)).scalars().all()

    return PaginatedAuditLogs(
        items=[AuditLogResponse.from_log(l) for l in logs],
        total_count=total,
        page=pagination.page,
        page_size=pagination.page_size,
        total_pages=max(1, -(-total // pagination.page_size)),
    )


@router.get("/actions", response_model=list[str], summary="Distinct audit actions (Admin only)")
async def list_audit_actions(current_user: AdminUser, db: DBSession):
    """Distinct action names present in the log — feeds the UI filter dropdown."""
    rows = (
        await db.execute(select(AuditLog.action).distinct().order_by(AuditLog.action))
    ).scalars().all()
    return list(rows)
