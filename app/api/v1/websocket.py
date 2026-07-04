"""
VIGIL-AI Cameroun — WebSocket Notification Handler
Real-time push notifications when AI analysis completes.

Architecture:
  Celery task → Redis pub/sub → WebSocket → Browser
"""
import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.config import settings
from app.core.security import decode_token

router = APIRouter(tags=["WebSockets"])
logger = logging.getLogger(__name__)


# ── Connection Manager ────────────────────────────────────────
class ConnectionManager:
    """Manages active WebSocket connections indexed by user_id."""

    def __init__(self):
        # user_id -> list of WebSocket connections (user can have multiple tabs)
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, user_id: str, websocket: WebSocket):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)
        logger.info(f"WebSocket connected: user={user_id} | active={self._count()}")

    def disconnect(self, user_id: str, websocket: WebSocket):
        if user_id in self.active_connections:
            self.active_connections[user_id].discard(websocket) if hasattr(
                self.active_connections[user_id], "discard"
            ) else None
            try:
                self.active_connections[user_id].remove(websocket)
            except ValueError:
                pass
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]
        logger.info(f"WebSocket disconnected: user={user_id} | active={self._count()}")

    async def send_to_user(self, user_id: str, message: dict):
        """Send a message to all connections for a specific user."""
        connections = self.active_connections.get(user_id, [])
        dead = []
        for ws in connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(user_id, ws)

    async def broadcast(self, message: dict):
        """Send a message to all connected users."""
        for user_id in list(self.active_connections.keys()):
            await self.send_to_user(user_id, message)

    def _count(self) -> int:
        return sum(len(v) for v in self.active_connections.values())


manager = ConnectionManager()


# ── Redis Subscriber (Background Task) ───────────────────────
async def redis_subscriber():
    """
    Listens to Redis pub/sub channel 'analysis_events'.
    When a Celery task publishes a result, routes it to the correct WebSocket.
    """
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.REDIS_URL)
        pubsub = r.pubsub()
        await pubsub.subscribe("analysis_events")
        logger.info("Redis pub/sub subscriber started")

        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    user_id = data.get("user_id")
                    if user_id:
                        await manager.send_to_user(user_id, data)
                        logger.info(f"Pushed notification to user {user_id}: {data.get('type')}")
                except Exception as e:
                    logger.error(f"Redis subscriber error: {e}")
    except Exception as e:
        logger.error(f"Redis pub/sub connection failed: {e}")
        # Non-fatal: WebSocket polling will be used as fallback


# ── WebSocket Endpoint ────────────────────────────────────────
@router.websocket("/ws/notifications")
async def websocket_notifications(websocket: WebSocket):
    """
    WebSocket endpoint for real-time notifications.

    Connection: ws://localhost:8000/ws/notifications?token=<jwt_token>
    Or: ws://localhost:8000/ws/notifications with Authorization header.

    Messages pushed to client:
      - analysis_complete: When AI analysis finishes
      - case_created: When a new case is auto-created
      - case_escalated: When a case is escalated to admin
    """
    # Authenticate via query param token
    token = websocket.query_params.get("token")
    if not token:
        # Try Authorization header
        auth_header = websocket.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        from jose import JWTError
        payload = decode_token(token)
        user_id = payload.get("sub")
        if not user_id or payload.get("type") != "access":
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect(user_id, websocket)

    try:
        # Send welcome message
        await websocket.send_json({
            "type": "connected",
            "message": "Connected to VIGIL-AI notification stream",
            "user_id": user_id,
        })

        # Keep connection alive — ping every 30s
        while True:
            try:
                # Wait for client ping (or just keep connection open)
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if data == "ping":
                    await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                # Send server-side ping
                await websocket.send_json({"type": "ping"})
            except WebSocketDisconnect:
                break

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error for user {user_id}: {e}")
    finally:
        manager.disconnect(user_id, websocket)
