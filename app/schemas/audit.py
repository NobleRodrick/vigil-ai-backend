"""
VIGIL-AI Cameroun — Audit Log Schemas
"""
import uuid
from datetime import datetime

from pydantic import BaseModel


class AuditLogResponse(BaseModel):
    id: int
    action: str
    resource_type: str | None
    resource_id: uuid.UUID | None
    details: dict | None
    ip_address: str | None
    created_at: datetime
    user_id: uuid.UUID | None
    user_email: str | None
    user_name: str | None

    model_config = {"from_attributes": True}

    @classmethod
    def from_log(cls, log: object) -> "AuditLogResponse":
        user = getattr(log, "user", None)
        return cls(
            id=log.id,  # type: ignore
            action=log.action,  # type: ignore
            resource_type=log.resource_type,  # type: ignore
            resource_id=log.resource_id,  # type: ignore
            details=log.details,  # type: ignore
            ip_address=str(log.ip_address) if log.ip_address else None,  # type: ignore
            created_at=log.created_at,  # type: ignore
            user_id=log.user_id,  # type: ignore
            user_email=user.email if user else None,
            user_name=user.full_name if user else None,
        )


class PaginatedAuditLogs(BaseModel):
    items: list[AuditLogResponse]
    total_count: int
    page: int
    page_size: int
    total_pages: int
