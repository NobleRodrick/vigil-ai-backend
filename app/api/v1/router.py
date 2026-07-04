"""
VIGIL-AI Cameroun — API v1 Router Aggregator
Registers all sub-routers under /api/v1
"""
from fastapi import APIRouter

from app.api.v1 import analytics, auth, cases, submissions, users, websocket

api_router = APIRouter()

# Auth
api_router.include_router(auth.router)

# Core resources
api_router.include_router(submissions.router)
api_router.include_router(cases.router)
api_router.include_router(analytics.router)
api_router.include_router(users.router)

# WebSocket (registered directly on the app, not under /api/v1 prefix)
ws_router = APIRouter()
ws_router.include_router(websocket.router)
