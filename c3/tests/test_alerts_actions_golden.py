"""Golden tests of POST /alerts/{alertId}/actions: C3 behaves exactly like the stub.

Each scenario runs twice on two fresh copies of the pristine seeded database, once
through the stub's router (stubs/c3.py) and once through C3. Compared, in this order:
  1. the status code and the JSON body;
  2. the alert row afterwards (every column);
  3. the alert's alert_action rows afterwards (every column);
  4. the alert.updated event: the stub's real HttpPublisher POSTs it to C1's
     /internal/events (recorded here through httpx.MockTransport); C3 hands it to its
     Publisher (recorded by a fake). The two must carry the same topic, payload and tenant;
  5. the feedback calls to C1's /events/{eventRef}/feedback: path, JSON and the bearer token.

Normalizers, the only changes made before comparing:
  drop_age_seconds           ageSeconds depends on the instant of the answer
  mask_request_times         body: updatedAt, acknowledgedAt and closedAt written by the request
                             (at or after its start) become "<request time>"
  mask_written_times         rows: a time column whose value differs from the pristine
                             snapshot becomes "<request time>", and the ts of a new action row
                             too; the times the seed wrote are still compared exactly
  stub_wire_to_publish_call  the stub's wire body {topic, payload{..., tenantId}, tenantId}
                             -> (topic, payload without tenantId, tenantId): the same three
                             things C3's Publisher receives (the HTTP publisher of step 6 puts
                             tenantId back on the wire)
"""
from __future__ import annotations

import json
import sqlite3
import types
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from c3_service.main import create_app
from c3_service.services.c1_client import C1Client
from c3_service.timeutil import utcnow
from conftest import (JWT_SECRET, SERVICE_SECRET, SNAPSHOT_DB, bearer, make_settings, service_token, sqlite_url,
                      token_of)

C1_BASE = "http://c1.test/api/v1"
TIME_KEYS = ("updatedAt", "acknowledgedAt", "closedAt")
TIME_COLUMNS = ("updated_at", "acknowledged_at", "closed_at")
REQUEST_TIME = "<request time>"


# -- recorders ---------------------------------------------------------------------------------------
class HttpRecorder:
    """An httpx transport that answers like C1 (202 / 201) and keeps every request it got."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append({"path": request.url.path, "json": json.loads(request.content or b"null"),
                           "authorization": request.headers.get("authorization")})
        return httpx.Response(202 if request.url.path.endswith("/internal/events") else 201, json={})

    def events(self) -> List[Dict[str, Any]]:
        return [c["json"] for c in self.calls if c["path"].endswith("/internal/events")]

    def feedback(self) -> List[Dict[str, Any]]:
        return [c for c in self.calls if c["path"].endswith("/feedback")]


class RecordingPublisher:
    def __init__(self) -> None:
        self.calls: List[Tuple[str, Dict[str, Any], int]] = []

    async def publish(self, topic: str, payload: Dict[str, Any], tenant_id: int) -> None:
        self.calls.append((topic, payload, tenant_id))


# -- the two apps on their own database copies ------------------------------------------------------
async def run_stub(db: Path, monkeypatch, method_args) -> Tuple[httpx.Response, HttpRecorder]:
    """The stub router with its real HttpPublisher; only the httpx module the stub sees is swapped."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    import stubs.common
    from app.db import make_engine as c1_make_engine
    from app.errors import install_error_handlers as c1_error_handlers
    from stubs.c3 import c3_router
    from stubs.common import HttpPublisher, StubContext

    recorder = HttpRecorder()
    fake_httpx = types.SimpleNamespace(
        HTTPError=httpx.HTTPError,
        AsyncClient=lambda **kwargs: httpx.AsyncClient(transport=recorder.transport, **kwargs))
    monkeypatch.setattr(stubs.common, "httpx", fake_httpx)  # only stubs.common's view of httpx
    engine = c1_make_engine(sqlite_url(db))
    stub_app = FastAPI()
    c1_error_handlers(stub_app)
    stub_app.include_router(c3_router, prefix="/api/v1")
    stub_app.state.stub_ctx = StubContext(async_sessionmaker(engine, expire_on_commit=False, autoflush=False), None,
                                          JWT_SECRET, SERVICE_SECRET, HttpPublisher(C1_BASE, SERVICE_SECRET))
    try:
        async with AsyncClient(transport=ASGITransport(app=stub_app), base_url="http://stub.test") as client:
            response = await client.post(*method_args[0], **method_args[1])
    finally:
        await engine.dispose()
    return response, recorder


async def run_c3(db: Path, mongo, method_args) -> Tuple[httpx.Response, RecordingPublisher, HttpRecorder]:
    publisher, recorder = RecordingPublisher(), HttpRecorder()
    c3_app = create_app(make_settings(database_url=sqlite_url(db), c1_base_url=C1_BASE), mongo=mongo,
                        publisher=publisher, c1_client=C1Client(C1_BASE, transport=recorder.transport))
    async with c3_app.router.lifespan_context(c3_app):
        async with AsyncClient(transport=ASGITransport(app=c3_app), base_url="http://c3.test") as client:
            response = await client.post(*method_args[0], **method_args[1])
    return response, publisher, recorder


# -- reading and normalizing ------------------------------------------------------------------------
def read_rows(db: Path, alert_id: Any) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    if not str(alert_id).isdigit():
        return None, []
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        alert = conn.execute("SELECT * FROM alert WHERE alert_id = ?", (int(alert_id),)).fetchone()
        actions = conn.execute("SELECT * FROM alert_action WHERE alert_id = ? ORDER BY action_id",
                               (int(alert_id),)).fetchall()
    return (dict(alert) if alert else None), [dict(a) for a in actions]


def drop_age_seconds(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: drop_age_seconds(v) for k, v in value.items() if k != "ageSeconds"}
    if isinstance(value, list):
        return [drop_age_seconds(v) for v in value]
    return value


def mask_request_times(body: Any, started: datetime) -> Any:
    if not isinstance(body, dict):
        return body
    out = dict(body)
    for key in TIME_KEYS:
        value = out.get(key)
        if isinstance(value, str) and datetime.fromisoformat(value.replace("Z", "")) >= started:
            out[key] = REQUEST_TIME
    return out


def mask_written_times(alert: Optional[Dict[str, Any]], actions: List[Dict[str, Any]],
                       before: Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]):
    before_alert, before_actions = before
    if alert is not None:
        alert = dict(alert)
        for column in TIME_COLUMNS:
            if before_alert is None or alert[column] != before_alert[column]:
                alert[column] = REQUEST_TIME
    old_ids = {a["action_id"] for a in before_actions}
    actions = [{**a, "ts": REQUEST_TIME} if a["action_id"] not in old_ids else a for a in actions]
    return alert, actions


def stub_wire_to_publish_call(body: Dict[str, Any]) -> Tuple[str, Dict[str, Any], Any]:
    payload = {k: v for k, v in body["payload"].items() if k != "tenantId"}
    assert body["payload"].get("tenantId") == body["tenantId"]
    return body["topic"], payload, body["tenantId"]


# -- the scenarios ----------------------------------------------------------------------------------
def pick(tenant_id: int, status: str) -> int:
    """The lowest alert id of a tenant in a status, in the pristine snapshot."""
    with sqlite3.connect(SNAPSHOT_DB) as conn:
        row = conn.execute("SELECT MIN(alert_id) FROM alert WHERE tenant_id = ? AND status = ?",
                           (tenant_id, status)).fetchone()
    assert row[0] is not None, f"the seed has no {status} alert in tenant {tenant_id}"
    return row[0]


ALERT_REFS = {"A:20482": lambda: 20482, "B:NEW": lambda: pick(2, "NEW"),
              "B:ACKNOWLEDGED": lambda: pick(2, "ACKNOWLEDGED"), "B:RESOLVED": lambda: pick(2, "RESOLVED"),
              "B:FALSE_ALARM": lambda: pick(2, "FALSE_ALARM"), "99999": lambda: 99999, "abc": lambda: "abc"}
ACK, RESOLVE, FALSE = {"action": "ACKNOWLEDGE"}, {"action": "RESOLVE"}, {"action": "FALSE_ALARM"}

# (id, who, alert ref, body, query params)
SCENARIOS = [
    # every role on a NEW alert
    ("owner-ack", "anh", "A:20482", ACK, {}),
    ("fleet-ack", "maria", "B:NEW", ACK, {}),
    ("staff-any-tenant-ack", "staff", "A:20482", ACK, {}),
    ("staff-tB-code-ack", "staff", "B:NEW", ACK, {"tenantId": "tB"}),
    ("admin-tenant-number-resolve", "admin", "A:20482", RESOLVE, {"tenantId": 1}),
    ("service-ack", "service", "A:20482", ACK, {"tenantId": 1}),
    ("service-resolve-no-feedback", "service", "B:NEW", RESOLVE, {"tenantId": 2}),
    ("rider-403", "rider", "A:20482", ACK, {}),
    ("iot-403", "iot", "A:20482", ACK, {"tenantId": 1}),
    ("no-token-401", None, "A:20482", ACK, {}),
    # NEW -> each action
    ("new-resolve-note", "maria", "B:NEW", {"action": "RESOLVE", "note": "Confirmed on site"}, {}),
    ("new-false-alarm-note", "maria", "B:NEW", {"action": "FALSE_ALARM", "note": "Construction noise"}, {}),
    ("owner-new-resolve", "anh", "A:20482", RESOLVE, {}),
    ("owner-new-false-alarm", "anh", "A:20482", FALSE, {}),
    # ACKNOWLEDGED -> RESOLVE / FALSE_ALARM
    ("ack-resolve", "maria", "B:ACKNOWLEDGED", RESOLVE, {}),
    ("ack-false-alarm", "maria", "B:ACKNOWLEDGED", {"action": "FALSE_ALARM", "note": "Street fair"}, {}),
    ("staff-ack-resolve", "staff", "B:ACKNOWLEDGED", RESOLVE, {"tenantId": 2}),
    # illegal transitions (409)
    ("ack-ack-409", "maria", "B:ACKNOWLEDGED", ACK, {}),
    ("resolved-ack-409", "maria", "B:RESOLVED", ACK, {}),
    ("resolved-resolve-409", "maria", "B:RESOLVED", RESOLVE, {}),
    ("resolved-false-alarm-409", "maria", "B:RESOLVED", FALSE, {}),
    ("false-alarm-ack-409", "maria", "B:FALSE_ALARM", ACK, {}),
    ("false-alarm-resolve-409", "maria", "B:FALSE_ALARM", RESOLVE, {}),
    ("false-alarm-false-alarm-409", "maria", "B:FALSE_ALARM", FALSE, {}),
    # another tenant's alert, unknown id (404)
    ("fleet-on-tenant-A-404", "maria", "A:20482", ACK, {}),
    ("owner-on-tenant-B-404", "anh", "B:NEW", ACK, {}),
    ("staff-scoped-to-tB-404", "staff", "A:20482", ACK, {"tenantId": 2}),
    ("unknown-id-404", "anh", "99999", ACK, {}),
    ("non-numeric-id-400", "anh", "abc", ACK, {}),
    # bad bodies (400)
    ("bad-action-400", "anh", "A:20482", {"action": "CLOSE"}, {}),
    ("lowercase-action-400", "anh", "A:20482", {"action": "acknowledge"}, {}),
    ("missing-action-400", "anh", "A:20482", {}, {}),
    ("note-501-400", "anh", "A:20482", {"action": "ACKNOWLEDGE", "note": "x" * 501}, {}),
    ("note-500-ok", "anh", "A:20482", {"action": "ACKNOWLEDGE", "note": "x" * 500}, {}),
    ("rider-bad-body-400", "rider", "A:20482", {"action": "CLOSE"}, {}),  # body is checked before the role
]


def _headers(who: Optional[str]) -> Dict[str, str]:
    if who is None:
        return {}
    return bearer(service_token()) if who == "service" else bearer(token_of(who))


@pytest.mark.parametrize("who,ref,body,params", [s[1:] for s in SCENARIOS], ids=[s[0] for s in SCENARIOS])
async def test_action_matches_stub(fresh_db_copy, mongo, monkeypatch, who, ref, body, params):
    alert_id = ALERT_REFS[ref]()
    stub_db, c3_db = fresh_db_copy("stub.db"), fresh_db_copy("c3.db")
    before = read_rows(SNAPSHOT_DB, alert_id)
    request = ((f"/api/v1/alerts/{alert_id}/actions",), {"json": body, "params": params, "headers": _headers(who)})
    started = utcnow().replace(microsecond=0)

    stub_response, stub_http = await run_stub(stub_db, monkeypatch, request)
    c3_response, c3_publisher, c3_http = await run_c3(c3_db, mongo, request)

    # 1. status and body
    assert c3_response.status_code == stub_response.status_code, (c3_response.json(), stub_response.json())
    expected_body = mask_request_times(drop_age_seconds(stub_response.json()), started)
    assert mask_request_times(drop_age_seconds(c3_response.json()), started) == expected_body
    # 2. and 3. the rows
    assert mask_written_times(*read_rows(c3_db, alert_id), before) == \
        mask_written_times(*read_rows(stub_db, alert_id), before)
    # 4. the published event
    assert [(t, p, tid) for t, p, tid in c3_publisher.calls] == [stub_wire_to_publish_call(e) for e in stub_http.events()]
    # 5. the feedback calls to C1
    assert c3_http.feedback() == stub_http.feedback()


def test_scenarios_cover_the_matrix():
    """Guards the scenario list: every outcome the step asks for is in it."""
    ids = {s[0] for s in SCENARIOS}
    assert len(ids) == len(SCENARIOS)
    for needed in ("owner-ack", "fleet-ack", "rider-403", "iot-403", "service-ack", "admin-tenant-number-resolve",
                   "new-resolve-note", "new-false-alarm-note", "ack-resolve", "ack-false-alarm", "ack-ack-409",
                   "resolved-resolve-409", "false-alarm-ack-409", "fleet-on-tenant-A-404", "unknown-id-404",
                   "bad-action-400", "note-501-400"):
        assert needed in ids
