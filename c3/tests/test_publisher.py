"""HttpPublisher with httpx.MockTransport: the request it sends, the service token, and the
best-effort rules (failures logged and swallowed, one retry on a connection error only,
the 2 s deadline, token renewal). Also which publisher create_app picks."""
from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
import jwt
import pytest
from pydantic import ValidationError

from c3_service.config import Settings
from c3_service.main import make_publisher
from c3_service.services.publisher import HttpPublisher, NoopPublisher, safe_publish
from conftest import SERVICE_SECRET

C1_BASE = "http://c1.test/api/v1"
UPDATED = {"alertId": 20482, "status": "ACKNOWLEDGED", "by": "Anh Nguyen", "action": "ACKNOWLEDGE", "vehicleId": "AV-01"}


class C1Double:
    """An httpx transport playing C1: each call takes the next behavior from `script` (the last one repeats)."""

    def __init__(self, *script) -> None:
        self.script = list(script) or ["202"]
        self.requests = []
        self.transport = httpx.MockTransport(self._handle)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        behavior = self.script[min(len(self.requests), len(self.script)) - 1]
        if behavior == "connect-error":
            raise httpx.ConnectError("connection refused", request=request)
        if behavior == "read-timeout":
            raise httpx.ReadTimeout("read timed out", request=request)
        if behavior == "hang":
            await asyncio.sleep(10)
        return httpx.Response(int(behavior), json={})


def _publisher(double: C1Double, **kwargs) -> HttpPublisher:
    return HttpPublisher(C1_BASE, SERVICE_SECRET, transport=double.transport, **kwargs)


# -- the request ------------------------------------------------------------------------------------
async def test_request_url_body_and_header():
    double = C1Double("202")
    assert await _publisher(double).publish("alert.updated", UPDATED, 1) is True
    (request,) = double.requests
    assert request.method == "POST" and str(request.url) == "http://c1.test/api/v1/internal/events"
    body = json.loads(request.content)
    assert body == {"topic": "alert.updated", "payload": {"tenantId": 1, **UPDATED}, "tenantId": 1}
    assert isinstance(body["tenantId"], int) and list(body["payload"])[0] == "tenantId"  # numeric, first, as the stub
    assert request.headers["content-type"] == "application/json"
    assert request.headers["authorization"].startswith("Bearer ")


async def test_token_claims():
    double = C1Double("202")
    await _publisher(double).publish("alert.updated", UPDATED, 1)
    claims = jwt.decode(double.requests[0].headers["authorization"][7:], SERVICE_SECRET, algorithms=["HS256"])
    assert claims["sub"] == "c3" and claims["role"] == "SERVICE"
    assert claims["exp"] - claims["iat"] == 3600 and abs(claims["iat"] - time.time()) < 5 and claims["jti"]


async def test_token_is_reused_then_renewed_five_minutes_before_expiry():
    now = [1_000_000.0]
    publisher = HttpPublisher(C1_BASE, SERVICE_SECRET, clock=lambda: now[0])
    first = publisher.service_token()
    now[0] += 3300                      # exactly 5 minutes left: still the same token
    assert publisher.service_token() == first
    now[0] += 1                         # less than 5 minutes left: a new one
    second = publisher.service_token()
    assert second != first
    claims = jwt.decode(second, SERVICE_SECRET, algorithms=["HS256"], options={"verify_exp": False})
    assert claims["iat"] == 1_003_301 and claims["exp"] == 1_006_901


# -- best effort ------------------------------------------------------------------------------------
@pytest.mark.parametrize("script,expected_calls,log_text", [
    (["500"], 1, "C1 refused alert.updated (alert 20482): 500"),
    (["401"], 1, "C1 refused alert.updated (alert 20482): 401"),
    (["read-timeout"], 1, "C1 did not answer alert.updated (alert 20482)"),
    (["connect-error"], 2, "C1 unreachable for alert.updated (alert 20482)"),
], ids=["500", "401", "timeout", "connection-error"])
async def test_failures_are_logged_and_swallowed(caplog, script, expected_calls, log_text):
    double = C1Double(*script)
    with caplog.at_level(logging.INFO, logger="scicp.c3.publisher"):
        assert await _publisher(double).publish("alert.updated", UPDATED, 1) is False  # no exception
    assert len(double.requests) == expected_calls
    assert log_text in caplog.text


async def test_one_retry_after_a_connection_error_then_success():
    double = C1Double("connect-error", "202")
    started = time.monotonic()
    assert await _publisher(double).publish("alert.updated", UPDATED, 1) is True
    assert len(double.requests) == 2 and time.monotonic() - started >= 0.5


@pytest.mark.parametrize("first", ["500", "read-timeout"])
async def test_no_retry_on_anything_but_a_connection_error(first):
    double = C1Double(first, "202")
    assert await _publisher(double).publish("alert.updated", UPDATED, 1) is False
    assert len(double.requests) == 1


async def test_never_waits_longer_than_the_timeout():
    double = C1Double("hang")
    started = time.monotonic()
    assert await _publisher(double, timeout=0.3).publish("alert.updated", UPDATED, 1) is False
    assert time.monotonic() - started < 0.6


async def test_no_retry_when_the_deadline_leaves_no_room_for_it():
    double = C1Double("connect-error", "202")
    assert await _publisher(double, timeout=0.4).publish("alert.updated", UPDATED, 1) is False
    assert len(double.requests) == 1  # 0.5 s pause would pass the 0.4 s deadline


async def test_alert_created_is_logged_with_its_alert_id(caplog):
    double = C1Double("500")
    with caplog.at_level(logging.WARNING, logger="scicp.c3.publisher"):
        await _publisher(double).publish("alert.created", {"alert": {"alertId": 30001}}, 2)
    assert "alert.created (alert 30001)" in caplog.text


async def test_safe_publish_stays_true_to_its_word_with_http():
    assert await safe_publish(_publisher(C1Double("500")), "alert.updated", UPDATED, 1) is True  # nothing raised


# -- which publisher create_app uses ----------------------------------------------------------------
def test_http_publisher_by_default():
    publisher = make_publisher(Settings(_env_file=None, c1_base_url=C1_BASE))
    assert isinstance(publisher, HttpPublisher) and publisher.url == f"{C1_BASE}/internal/events"
    assert publisher.timeout == 2.0


@pytest.mark.parametrize("settings", [dict(publisher="noop"), dict(publisher="NOOP"), dict(c1_base_url="")])
def test_noop_publisher_when_asked_or_without_c1(settings):
    assert isinstance(make_publisher(Settings(_env_file=None, **settings)), NoopPublisher)


def test_unknown_publisher_setting_is_refused():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, publisher="kafka")
