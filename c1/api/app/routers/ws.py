"""WsRouter: WebSocket /ws -> WsHub.connect."""
from __future__ import annotations

from fastapi import APIRouter, WebSocket

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.app.state.container.hub.connect(ws)
