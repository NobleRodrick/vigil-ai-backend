# ═══════════════════════════════════════════════════════════════
# VIGIL-AI Cameroun — Dockerfile
# Multi-stage build: development + production targets
# ═══════════════════════════════════════════════════════════════

FROM python:3.12-slim AS base

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Development stage ─────────────────────────────────────────
FROM base AS development

# Source code is mounted as a volume in docker-compose
COPY . .

# Create upload directory
RUN mkdir -p /app/uploads

EXPOSE 8000

# ── Production stage ──────────────────────────────────────────
FROM base AS production

COPY . .

RUN mkdir -p /app/uploads && \
    useradd -r -u 1001 vigilai && \
    chown -R vigilai:vigilai /app

USER vigilai

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
