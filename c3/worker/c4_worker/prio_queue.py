"""Bounded two-level priority queue for the audio events (edge.priority HIGH before NORMAL).

FIFO within a level. When the queue is full a new event pushes out the OLDEST NORMAL one; if the
queue is full of HIGH events the new event itself is dropped. Every drop is counted and the dropped
item is returned so the caller can acknowledge its MQTT message (otherwise it would hold one of the
broker's in-flight slots until the next reconnect).

Note: the worker acknowledges an MQTT message only after the event is stored. Mosquitto's default
max_inflight_messages is 20, so with that broker config at most about 20 events are ever in this
queue; the 200 bound and the drop rule matter when the broker allows more in flight.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Optional


@dataclass
class QueuedEvent:
    tenant_id: str
    vehicle_id: str
    payload: Dict[str, Any]
    mid: Optional[int] = None           # MQTT message id, acknowledged after the event is stored
    high: bool = False
    received_at: float = field(default=0.0)

    @classmethod
    def build(cls, tenant_id: str, vehicle_id: str, payload: Dict[str, Any], mid: Optional[int] = None,
              received_at: float = 0.0) -> "QueuedEvent":
        edge = payload.get("edge") if isinstance(payload.get("edge"), dict) else {}
        return cls(tenant_id, vehicle_id, payload, mid, str(edge.get("priority", "")).upper() == "HIGH", received_at)


class PriorityEventQueue:
    def __init__(self, maxsize: int = 200) -> None:
        self.maxsize = maxsize
        self._high: Deque[QueuedEvent] = deque()
        self._normal: Deque[QueuedEvent] = deque()
        self._ready = asyncio.Event()
        self.dropped = 0

    def __len__(self) -> int:
        return len(self._high) + len(self._normal)

    def put(self, item: QueuedEvent) -> Optional[QueuedEvent]:
        """Never blocks. Returns the dropped event, if any (it may be `item` itself)."""
        dropped: Optional[QueuedEvent] = None
        if len(self) >= self.maxsize:
            if self._normal:
                dropped = self._normal.popleft()
            else:
                self.dropped += 1
                return item
            self.dropped += 1
        (self._high if item.high else self._normal).append(item)
        self._ready.set()
        return dropped

    def get_nowait(self) -> Optional[QueuedEvent]:
        if self._high:
            item = self._high.popleft()
        elif self._normal:
            item = self._normal.popleft()
        else:
            return None
        if not len(self):
            self._ready.clear()
        return item

    async def get(self) -> QueuedEvent:
        while True:
            item = self.get_nowait()
            if item is not None:
                return item
            await self._ready.wait()
