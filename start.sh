#!/usr/bin/env bash
# VIGIL-AI Cameroun — Render free-tier combined start script
#
# Render's free Web Service plan does not offer a separate Background
# Worker service type, so this script runs the Celery worker and the
# FastAPI server as two processes inside the same container:
#   - Celery worker runs in the background (concurrency=1 — Render's
#     free instance has 512MB RAM shared between both processes, so a
#     single worker process is the safe default; raise it only if you
#     upgrade to a paid instance type with more memory)
#   - Uvicorn runs in the foreground, bound to $PORT, which is what
#     Render actually monitors for health checks and routes traffic to
#
# --without-mingle/--without-gossip/--without-heartbeat: Upstash's free
# Redis tier periodically closes idle connections, which caused repeated
# reconnect failures during the mingle/gossip handshake in testing. These
# flags skip that handshake entirely — safe since we're running a single
# worker with nothing to discover.
#
# This file is invoked by setting Render's "Start Command" to:
#   bash start.sh
set -e

celery -A app.workers.celery_app worker \
  --loglevel=info -Q celery,analysis,alerts,default --concurrency=1 \
  --without-mingle --without-gossip --without-heartbeat &

uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"