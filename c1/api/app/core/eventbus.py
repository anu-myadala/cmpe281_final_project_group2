"""EventBus: in-process publish/subscribe (Redis in the full-scale design).

Topics: alert.created, alert.updated, device.status, model.activated.
Handlers may be coroutine functions or plain callables; a failing handler is
logged and never breaks the publisher.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from typing import Any, Callable, Dict, List

log = logging.getLogger("scicp.eventbus")

Handler = Callable[[str, Dict[str, Any]], Any]


class EventBus:
    def __init__(self) -> None:
        self._handlers: Dict[str, List[Handler]] = defaultdict(list)
        self._all: List[Handler] = []

    def subscribe(self, topic: str, handler: Handler) -> None:
        """Subscribe to one topic, or to every topic with topic="*"."""
        if topic == "*":
            self._all.append(handler)
        else:
            self._handlers[topic].append(handler)

    async def publish(self, topic: str, payload: Dict[str, Any]) -> None:
        for handler in list(self._handlers.get(topic, [])) + list(self._all):
            try:
                result = handler(topic, payload)
                if inspect.isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001 - a bad subscriber must not break the publisher
                log.exception("EventBus handler failed for %s", topic)

    def publish_nowait(self, topic: str, payload: Dict[str, Any]) -> None:
        """Schedule a publish from synchronous code on the running loop."""
        asyncio.get_running_loop().create_task(self.publish(topic, payload))
