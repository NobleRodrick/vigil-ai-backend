"""
VIGIL-AI Cameroun — Celery Application
Background task processor for AI analysis and notifications
"""
import ssl

from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "vigil_ai",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Africa/Douala",
    enable_utc=True,

    # SSL settings for Upstash rediss:// URLs
    broker_use_ssl={"ssl_cert_reqs": ssl.CERT_NONE},
    redis_backend_use_ssl={"ssl_cert_reqs": ssl.CERT_NONE},

    # Queue routing
    task_routes={
        "app.workers.tasks.run_analysis": {"queue": "analysis"},
        "app.workers.tasks.send_alert_email": {"queue": "alerts"},
        "app.workers.tasks.generate_monthly_report": {"queue": "default"},
    },

    # Retry settings
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,

    # Result expiry
    result_expires=3600 * 24,  # Keep results 24 hours

    # Beat schedule (periodic tasks)
    beat_schedule={
        "generate-monthly-report": {
            "task": "app.workers.tasks.generate_monthly_report",
            "schedule": crontab(day_of_month=1, hour=3, minute=0),  # 1st of month at 3am
        },
        "cleanup-old-frames": {
            "task": "app.workers.tasks.cleanup_video_frames",
            "schedule": crontab(hour=2, minute=0),  # Daily at 2am
        },
    },

    # Reduce Upstash disconnect issues
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "visibility_timeout": 3600,
        "socket_keepalive": True,
        "socket_connect_timeout": 30,
        "retry_on_timeout": True,
        "max_retries": 10,
        "interval_start": 0,
        "interval_step": 0.5,
        "interval_max": 3,
    },
)