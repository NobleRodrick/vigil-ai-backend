"""
VIGIL-AI Cameroun — FastAPI Application Entry Point
National Cybersecurity AI Detection Platform

Run locally:  uvicorn app.main:app --reload
Run in Docker: uvicorn app.main:app --host 0.0.0.0 --port 8000

API Docs: http://localhost:8000/docs
Redoc:    http://localhost:8000/redoc
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.api.v1.websocket import redis_subscriber, router as ws_router
from app.config import settings
from app.core.exceptions import (
    VigilAIException,
    http_exception_handler,
    unhandled_exception_handler,
    vigil_exception_handler,
)
from app.core.middleware import RequestLoggingMiddleware, SecurityHeadersMiddleware

# ── Logging Setup ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Lifespan (startup / shutdown) ─────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    import asyncio

    from app.database import engine

    # Create upload directory
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    logger.info(f"Upload directory ready: {settings.UPLOAD_DIR}")

    # Start Redis pub/sub subscriber in the background
    # (delivers WebSocket notifications when Celery tasks complete)
    subscriber_task = asyncio.create_task(redis_subscriber())
    logger.info("Redis pub/sub subscriber started")

    logger.info(
        f"🚀 VIGIL-AI Cameroun v{settings.APP_VERSION} started "
        f"| env={settings.ENVIRONMENT} | debug={settings.DEBUG}"
    )

    yield  # App is running

    # Shutdown
    subscriber_task.cancel()
    await engine.dispose()
    logger.info("VIGIL-AI Cameroun shut down cleanly")


# ── FastAPI App ───────────────────────────────────────────────
app = FastAPI(
    title="VIGIL-AI Cameroun API",
    description="""
## VIGIL-AI Cameroun — National Cybersecurity AI Detection Platform

API for detecting AI-generated content, deepfakes, and synthetic media in Cameroonian cyberspace.

### Roles
| Role | Access |
|------|--------|
| **admin** | Full access — user management, all cases, audit logs, CSV export |
| **analyst** | Submit content, manage cases, view all dashboards |
| **viewer** | Read-only access to dashboards and reports |

### Authentication
All endpoints except `/api/v1/auth/login` require a Bearer JWT token:
```
Authorization: Bearer <access_token>
```

### Free AI Models Used
- **Text**: HuggingFace `Hello-SimpleAI/chatgpt-detector-roberta`
- **Image**: HuggingFace `Wvolf/ViT-Deepfake-Detection`
- **Audio**: HuggingFace `facebook/wav2vec2-base`
- All models are accessed via the **free** HuggingFace Inference API

Built for the **5th Digital Innovation Week 2026 — MINPOSTEL Cameroun**
    """,
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGIN_LIST,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Response-Time", "Content-Disposition"],
)

# ── Custom Middleware ─────────────────────────────────────────
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestLoggingMiddleware)

# ── Exception Handlers ────────────────────────────────────────
app.add_exception_handler(VigilAIException, vigil_exception_handler)          # type: ignore
app.add_exception_handler(HTTPException, http_exception_handler)               # type: ignore
app.add_exception_handler(Exception, unhandled_exception_handler)              # type: ignore

# ── Routers ───────────────────────────────────────────────────
app.include_router(api_router, prefix="/api/v1")
app.include_router(ws_router)  # WebSocket at /ws/notifications

# ── Static Files (uploaded media, served by FastAPI in dev) ───
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")


# ── Health Check ──────────────────────────────────────────────
@app.get("/health", tags=["Health"], summary="Health check endpoint")
async def health_check():
    """Returns the health status of the API and its dependencies."""
    import asyncio
    from datetime import UTC, datetime

    checks = {
        "api": "ok",
        "database": "unknown",
        "redis": "unknown",
        "celery": "unknown",
    }

    # Check DB
    try:
        from sqlalchemy import text
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    # Check Redis
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.REDIS_URL)
        await r.ping()
        await r.aclose()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    # Check Celery (via Redis broker)
    # Check Celery broker connectivity
    # (Upstash free tier doesn't support inspect().ping() reliably due to
    # pub/sub limitations, so we verify broker connectivity directly instead)
    try:
        from app.workers.celery_app import celery_app
        with celery_app.connection_for_write() as conn:
            conn.ensure_connection(max_retries=1, timeout=5)
        checks["celery"] = "ok"
    except Exception as e:
        checks["celery"] = f"error: {e}"

    all_ok = all(v == "ok" for v in checks.values())
    return {
        "status": "healthy" if all_ok else "degraded",
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "timestamp": datetime.now(UTC).isoformat(),
        "checks": checks,
    }


@app.get("/", tags=["Health"], include_in_schema=False)
async def root():
    return {
        "name": "VIGIL-AI Cameroun API",
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "health": "/health",
    }
