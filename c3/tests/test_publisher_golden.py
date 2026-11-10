"""Golden tests of the events C3 sends to C1, against the stub.

1. The stub's real HttpPublisher (stubs/common.py) and C3's HttpPublisher publish the same
   alert.updated: the HTTP requests must be equal (method, URL, body bytes, every header),
   except the token value. Both tokens must be service tokens C1 accepts. Known, agreed
   differences inside the token: sub "c3" instead of "stubs", and 1 hour instead of 24.
2. alert_created_payload() builds the payload the stub's generator sends for alert.created
   (stubs/generator.py _create_alert, the stub's only builder for it), checked by running
   that function and rebuilding its alert with C3's own code from the row it wrote.
"""
from __future__ import annotations

import json
import random
import types

import httpx
import jwt
from sqlalchemy import select

from c3_service.db import make_engine, make_session_factory
from c3_service.models import Alert
from c3_service.services.alert_query import alert_row
from c3_service.services.publisher import HttpPublisher, alert_created_payload
from conftest import SERVICE_SECRET, sqlite_url

C1_BASE = "http://c1.test/api/v1"


class Recorder:
    def __init__(self) -> None:
        self.requests = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(202)


async def test_same_request_as_the_stub_for_alert_updated(monkeypatch):
    import stubs.common
    from stubs.common import HttpPublisher as StubHttpPublisher

    stub_side, c3_side = Recorder(), Recorder()
    monkeypatch.setattr(stubs.common, "httpx", types.SimpleNamespace(
        HTTPError=httpx.HTTPError, AsyncClient=lambda **kw: httpx.AsyncClient(transport=stub_side.transport, **kw)))
    updated = {"alertId": 20482, "status": "ACKNOWLEDGED", "by": "Anh Nguyen", "action": "ACKNOWLEDGE",
               "vehicleId": "AV-01"}

    # The stub's action route calls publish("alert.updated", {"tenantId": ..., "alertId": ..., ...}).
    await StubHttpPublisher(C1_BASE, SERVICE_SECRET).publish("alert.updated", {"tenantId": 1, **updated})
    await HttpPublisher(C1_BASE, SERVICE_SECRET, transport=c3_side.transport).publish("alert.updated", updated, 1)

    (stub_request,), (c3_request,) = stub_side.requests, c3_side.requests
    assert (c3_request.method, str(c3_request.url)) == (stub_request.method, str(stub_request.url))
    assert c3_request.content == stub_request.content  # the body, byte for byte
    assert json.loads(c3_request.content) == {"topic": "alert.updated", "payload": {"tenantId": 1, **updated},
                                              "tenantId": 1}

    def without_token(request):
        return {k: v for k, v in request.headers.items() if k != "authorization"}

    assert without_token(c3_request) == without_token(stub_request)
    for request, subject, lifetime in ((stub_request, "stubs", 24 * 3600), (c3_request, "c3", 3600)):
        scheme, token = request.headers["authorization"].split(" ", 1)
        claims = jwt.decode(token, SERVICE_SECRET, algorithms=["HS256"])
        assert scheme == "Bearer" and claims["role"] == "SERVICE"
        assert (claims["sub"], claims["exp"] - claims["iat"]) == (subject, lifetime)  # the agreed differences


async def test_alert_created_payload_matches_the_stubs_builder(fresh_db_copy):
    from mongomock_motor import AsyncMongoMockClient
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db import make_engine as c1_make_engine
    from stubs.generator import CarState, _create_alert

    class Captured:
        def __init__(self) -> None:
            self.events = []

        async def publish(self, topic, payload):
            self.events.append((topic, payload))

    db, mongo, captured = fresh_db_copy(), AsyncMongoMockClient()["scicp"], Captured()
    stub_engine = c1_make_engine(sqlite_url(db))
    rng = random.Random(281)
    try:
        for code, tenant_id, tenant_code in (("AV-12", 2, "tB"), ("AV-01", 1, "tA"), ("AV-10", 2, "tB")):
            car = CarState(code, tenant_id, tenant_code, rng)
            await _create_alert(async_sessionmaker(stub_engine, expire_on_commit=False), mongo, captured, car, rng)
    finally:
        await stub_engine.dispose()

    engine = make_engine(sqlite_url(db))
    try:
        async with make_session_factory(engine)() as session:
            for topic, payload in captured.events:
                assert topic == "alert.created"
                row = (await session.execute(select(Alert).where(Alert.alert_id == payload["alert"]["alertId"]))).scalar_one()
                ours = alert_created_payload(alert_row(row, {}))
                assert {"tenantId": row.tenant_id, **ours} == payload       # same fields, same values
                assert list(ours["alert"]) == list(payload["alert"])         # and the same order
    finally:
        await engine.dispose()
    assert len(captured.events) == 3
