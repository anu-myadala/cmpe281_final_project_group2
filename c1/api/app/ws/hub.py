"""WsHub: WebSocket connections, rooms and the fan-out of EventBus events.

Protocol (Part I, Table 15): the client's first message is {type: "auth",
token}; a bad token closes the socket with 4401. The verified claims decide
the rooms once: a tenant role joins tenant:{tenantId}, a staff role joins
staff. The server pings every 30 s and closes after three missed pongs.
on_event strips tenantId from the payload before the message leaves.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any, Callable, Dict, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

from ..core.eventbus import EventBus
from ..core.security import Claims, TokenService
from ..errors import ApiError
from ..models import STAFF_ROLES

log = logging.getLogger("scicp.ws")

WS_TOPICS = ("alert.created", "alert.updated", "device.status", "model.activated", "model.deployed")
# Model registry messages concern the platform staff only.
STAFF_ONLY_TOPICS = ("model.activated", "model.deployed")
AUTH_TIMEOUT_SECONDS = 10
PING_INTERVAL_SECONDS = 30
MAX_MISSED_PINGS = 3


class _Connection:
    def __init__(self, ws: WebSocket, claims: Claims, rooms: Set[str]) -> None:
        self.ws = ws
        self.claims = claims
        self.rooms = rooms
        self.missed_pings = 0
        self.lock = asyncio.Lock()

    async def send(self, message: Dict[str, Any]) -> None:
        async with self.lock:
            await self.ws.send_text(json.dumps(message, default=str))


class WsHub:
    def __init__(self, bus: EventBus, tokens: TokenService,
                 tenant_resolver: Optional[Callable[[Any], Any]] = None) -> None:
        self.tokens = tokens
        self.rooms: Dict[str, Set[_Connection]] = defaultdict(set)
        self.ping_interval = PING_INTERVAL_SECONDS
        # Optional: maps a tenant code (tA) used by C2/C3 to the numeric tenant id.
        self.tenant_resolver = tenant_resolver
        for topic in WS_TOPICS:
            bus.subscribe(topic, self.on_event)

    # -- rooms --------------------------------------------------------------------
    @staticmethod
    def rooms_for(claims: Claims) -> Set[str]:
        if claims.role in STAFF_ROLES:
            return {"staff"}
        return {f"tenant:{claims.tenant_id}"}

    def connection_count(self, room: str) -> int:
        return len(self.rooms.get(room, ()))

    async def broadcast(self, room: str, message: Dict[str, Any]) -> None:
        for conn in list(self.rooms.get(room, ())):
            try:
                await conn.send(message)
            except Exception:  # noqa: BLE001 - a closed socket is dropped; the client reconnects
                self._drop(conn)

    def _drop(self, conn: _Connection) -> None:
        for room in conn.rooms:
            self.rooms[room].discard(conn)

    # -- EventBus -> rooms ----------------------------------------------------------
    async def on_event(self, topic: str, payload: Dict[str, Any]) -> None:
        if topic not in WS_TOPICS:
            return
        body = dict(payload or {})
        tenant_id = body.pop("tenantId", None)
        message = {"type": topic, **body}
        if topic in STAFF_ONLY_TOPICS:
            await self.broadcast("staff", message)
            return
        tenant_id = await self._resolve_tenant(tenant_id)
        if tenant_id is not None:
            await self.broadcast(f"tenant:{tenant_id}", message)
        await self.broadcast("staff", message)

    async def _resolve_tenant(self, value: Any) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        if self.tenant_resolver is not None:
            result = self.tenant_resolver(value)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        return None

    # -- one connection -----------------------------------------------------------------
    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        claims = await self._authenticate(ws)
        if claims is None:
            return
        conn = _Connection(ws, claims, self.rooms_for(claims))
        for room in conn.rooms:
            self.rooms[room].add(conn)
        log.info("ws connected user=%s rooms=%s", claims.user_id, sorted(conn.rooms))
        heartbeat = asyncio.create_task(self._heartbeat(conn))
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(message, dict) and message.get("type") == "pong":
                    conn.missed_pings = 0
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001
            log.debug("ws closed: %s", exc)
        finally:
            heartbeat.cancel()
            self._drop(conn)

    async def _authenticate(self, ws: WebSocket) -> Optional[Claims]:
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=AUTH_TIMEOUT_SECONDS)
            message = json.loads(raw)
            if not isinstance(message, dict) or message.get("type") != "auth":
                raise ValueError("first message must be auth")
            return self.tokens.verify(str(message.get("token", "")))
        except (asyncio.TimeoutError, ValueError, ApiError, WebSocketDisconnect, KeyError):
            try:
                await ws.close(code=4401, reason="unauthorized")
            except Exception:  # noqa: BLE001
                pass
            return None

    async def _heartbeat(self, conn: _Connection) -> None:
        try:
            while True:
                await asyncio.sleep(self.ping_interval)
                if conn.missed_pings >= MAX_MISSED_PINGS:
                    await conn.ws.close(code=4408, reason="heartbeat timeout")
                    return
                conn.missed_pings += 1
                await conn.send({"type": "ping"})
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the receive loop notices the closed socket
            pass
