"""EventBus: in-process publish/subscribe.

Topics: alert.created, alert.updated, device.status, model.activated, model.deployed.
Handlers may be coroutine functions or plain callables; a failing handler is
logged and never breaks the publisher.

With several API replicas behind the load balancer, an event that reaches one
replica (POST /internal/events from C2 or C3, a model activation) must also reach
the WebSocket clients and caches of the others. A forwarder (core/fanout.py, MQTT)
then copies every published event to the other replicas, which deliver it with
deliver() and do not forward it again.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("scicp.eventbus")

Handler = Callable[[str, Dict[str, Any]], Any]


class EventBus:
    def __init__(self) -> None:
        self._handlers: Dict[str, List[Handler]] = defaultdict(list)
        self._all: List[Handler] = []
        # Set by MqttFanout when EVENT_FANOUT=1: called with (topic, payload) after the local delivery.
        self.forwarder: Optional[Callable[[str, Dict[str, Any]], Any]] = None

    def subscribe(self, topic: str, handler: Handler) -> None:
        """Subscribe to one topic, or to every topic with topic="*"."""
        if topic == "*":
            self._all.append(handler)
        else:
            self._handlers[topic].append(handler)

    async def publish(self, topic: str, payload: Dict[str, Any]) -> None:
        """Deliver to this process's subscribers, then copy the event to the other replicas."""
        await self.deliver(topic, payload)
        if self.forwarder is not None:
            try:
                self.forwarder(topic, payload)
            except Exception:  # noqa: BLE001 - the local delivery already happened
                log.exception("EventBus forwarder failed for %s", topic)

    async def deliver(self, topic: str, payload: Dict[str, Any]) -> None:
        """Run the local subscribers only (also used for events that come from another replica)."""
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
