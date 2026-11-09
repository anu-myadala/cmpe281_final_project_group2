"""Publisher: how C2 hands device.status to C1's EventBus (Section 2.9 of the C2 design).

Adapted from C3's publisher (c3/c3_service/services/publisher.py) so both components send
events the same way. C2 holds no WebSockets: C1's WsHub sends each event to the tenant room
and the staff room.

  HttpPublisher   the default: POST {C1_BASE_URL}/internal/events with a service token
  NoopPublisher   PUBLISHER=noop: drops the events (tests, developer mode)
  RecordingPublisher  keeps the events in a list (tests)

safe_publish() is the only way the services publish: events go out after the COMMIT, and a
failure is logged, never raised.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Callable, Dict, Optional, Protocol

import httpx
import jwt

log = logging.getLogger("scicp.c2.publisher")

TOKEN_TTL_S = 3600          # the service token lives 1 hour ...
TOKEN_RENEW_BEFORE_S = 300  # ... and is replaced 5 minutes before it expires
RETRY_DELAY_S = 0.5         # one retry, only after a connection error


class Publisher(Protocol):
    async def publish(self, topic: str, payload: Dict[str, Any], tenant_id: int) -> None:
        """Send one event; tenant_id (numeric) is the routing key for the tenant's room."""


class NoopPublisher:
    """Drops every event (PUBLISHER=noop); logs it so the developer can see what would be sent."""

    async def publish(self, topic: str, payload: Dict[str, Any], tenant_id: int) -> None:
        log.info("not published (PUBLISHER=noop): %s tenant %s %s", topic, tenant_id, payload)


def _alert_id(payload: Dict[str, Any]) -> Any:
    """For the log: alert.updated carries alertId, alert.created carries alert.alertId."""
    return payload.get("alertId", (payload.get("alert") or {}).get("alertId"))


class HttpPublisher:
    """POST {c1_base_url}/internal/events {topic, payload, tenantId} with a service token (Part I, Section 2.8).

    The request is the stub's HttpPublisher's byte for byte (payload starts with tenantId,
    the only header is Authorization). It is best effort: a timeout, a connection error or a
    non-2xx answer is logged with the topic and the alert id and publish() returns False;
    it never raises. A connection error is retried once after 0.5 s. Both attempts and the
    pause share one deadline (`timeout`, 2 s), so the request that published never waits longer.
    """

    def __init__(self, c1_base_url: str, service_token_secret: str, timeout: float = 2.0,
                 transport: Optional[httpx.AsyncBaseTransport] = None, clock: Callable[[], float] = time.time) -> None:
        self.url = f"{c1_base_url.rstrip('/')}/internal/events"
        self.secret = service_token_secret
        self.timeout = timeout
        self.transport = transport
        self.clock = clock  # the tests move time to check the renewal
        self._token: Optional[str] = None
        self._token_exp = 0

    def service_token(self) -> str:
        """HS256 with SERVICE_TOKEN_SECRET, {sub: "c2", role: SERVICE, iat, exp, jti}; created once, renewed early."""
        now = int(self.clock())
        if self._token is None or now > self._token_exp - TOKEN_RENEW_BEFORE_S:
            self._token_exp = now + TOKEN_TTL_S
            self._token = jwt.encode({"sub": "c2", "role": "SERVICE", "iat": now, "exp": self._token_exp,
                                      "jti": uuid.uuid4().hex}, self.secret, algorithm="HS256")
        return self._token

    async def publish(self, topic: str, payload: Dict[str, Any], tenant_id: int) -> bool:
        body = {"topic": topic, "payload": {"tenantId": tenant_id, **payload}, "tenantId": tenant_id}
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout
        for attempt in (1, 2):
            remaining = deadline - loop.time()
            try:
                async with httpx.AsyncClient(timeout=remaining, transport=self.transport) as client:
                    response = await asyncio.wait_for(
                        client.post(self.url, json=body, headers={"Authorization": f"Bearer {self.service_token()}"}),
                        timeout=remaining)
            except httpx.ConnectError as exc:
                if attempt == 1 and deadline - loop.time() > RETRY_DELAY_S:
                    log.info("C1 unreachable for %s (%s): %s; retrying once", topic, _what(payload), exc)
                    await asyncio.sleep(RETRY_DELAY_S)
                    continue
                log.warning("C1 unreachable for %s (%s): %s", topic, _what(payload), exc)
                return False
            except (httpx.TimeoutException, asyncio.TimeoutError):
                log.warning("C1 did not answer %s (%s) within %.1f s", topic, _what(payload), self.timeout)
                return False
            except httpx.HTTPError as exc:
                log.warning("publishing %s (%s) failed: %s", topic, _what(payload), exc)
                return False
            if not 200 <= response.status_code < 300:
                log.warning("C1 refused %s (%s): %s %s", topic, _what(payload), response.status_code,
                            response.text[:200])
                return False
            return True
        return False


async def safe_publish(publisher: Publisher, topic: str, payload: Dict[str, Any], tenant_id: int) -> bool:
    """Publish; on any error log it and return False. The caller's work is already committed."""
    try:
        await publisher.publish(topic, payload, tenant_id)
        return True
    except Exception as exc:  # noqa: BLE001 - an event that is lost must never fail the request
        log.warning("publishing %s for tenant %s failed: %s", topic, tenant_id, exc)
        return False


class RecordingPublisher:
    """Keeps every event (tests)."""

    def __init__(self) -> None:
        self.events: list = []

    async def publish(self, topic: str, payload: Dict[str, Any], tenant_id: int) -> bool:
        self.events.append((topic, dict(payload), tenant_id))
        return True
