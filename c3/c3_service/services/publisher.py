"""Publisher: how C3 hands alert.created / alert.updated to C1's EventBus (Part III,
Section 2.5, Table 87; the EventBus row of Table 88).

C3 holds no WebSockets: C1's WsHub sends each event to the tenant's room and the
staff room. Publisher is an interface (a Protocol), so the transport can change
without touching the services. In this step the app uses NoopPublisher and the
tests a recording fake; step 6 adds the HTTP publisher (POST /internal/events).
safe_publish() is the only way the services publish: events go out after the
COMMIT, and a failure is logged, never raised, so it can't undo or fail an action.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Protocol

log = logging.getLogger("scicp.c3.publisher")


class Publisher(Protocol):
    async def publish(self, topic: str, payload: Dict[str, Any], tenant_id: int) -> None:
        """Send one event; tenant_id (numeric) is the routing key for the tenant's room."""


class NoopPublisher:
    """Drops every event (until step 6); logs it so the developer can see what would be sent."""

    async def publish(self, topic: str, payload: Dict[str, Any], tenant_id: int) -> None:
        log.info("not published (no publisher configured): %s tenant %s %s", topic, tenant_id, payload)


async def safe_publish(publisher: Publisher, topic: str, payload: Dict[str, Any], tenant_id: int) -> bool:
    """Publish; on any error log it and return False. The caller's work is already committed."""
    try:
        await publisher.publish(topic, payload, tenant_id)
        return True
    except Exception as exc:  # noqa: BLE001 - an event that is lost must never fail the request
        log.warning("publishing %s for tenant %s failed: %s", topic, tenant_id, exc)
        return False
