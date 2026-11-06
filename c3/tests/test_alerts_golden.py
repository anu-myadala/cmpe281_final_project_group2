"""Golden tests: C3 answers exactly like the C3 stub (stubs/c3.py).

The stub's router runs in this process on the same seeded SQLite file and the same
MongoDB mock as the C3 app. Every request below goes to both with the same token, and
the status code and the JSON body must be equal. Before comparing, only two things are
changed, each by a named normalizer:

  drop_age_seconds              ageSeconds depends on the instant of the call
  stub_notifications_to_empty   decision D6: C3 lists the real notification rows; the stub
                                derives SENT/SUPPRESSED entries from the subscription. Every
                                alert compared here was created by the seed (the stub's data),
                                so it has no rows in C3: C3 must answer [] for it, which the
                                comparison enforces. Alerts created through C3's result call
                                list their real rows (tests/test_notifications.py).

Known difference that the data cannot show (decision D1): the map takes the alert row's
position before the audio event's; the stub does the reverse. The seed and the generator
write the same coordinates to both, so the pins are equal and nothing is normalized for it.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from c3_service.models import Alert
from c3_service.timeutil import iso, utcnow
from conftest import DB_URL, JWT_SECRET, SERVICE_SECRET, bearer, service_token, token_of


class _NoPublisher:
    """The read routes publish nothing; this stands in for the stub's HTTP publisher."""
    async def publish(self, topic, payload):
        raise AssertionError(f"a read route published {topic}")

    async def feedback(self, event_ref, label, caller):
        raise AssertionError("a read route posted feedback")


@pytest_asyncio.fixture(scope="module")
async def stub_client(seeded, mongo):
    """The stub's C3 router under /api/v1, exactly as stubs/standalone.py serves it on :8003."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db import make_engine as c1_make_engine
    from app.errors import install_error_handlers as c1_error_handlers
    from stubs.c3 import c3_router
    from stubs.common import StubContext

    engine = c1_make_engine(DB_URL)
    stub_app = FastAPI()
    c1_error_handlers(stub_app)
    stub_app.include_router(c3_router, prefix="/api/v1")
    stub_app.state.stub_ctx = StubContext(async_sessionmaker(engine, expire_on_commit=False, autoflush=False), mongo,
                                          JWT_SECRET, SERVICE_SECRET, _NoPublisher())
    async with AsyncClient(transport=ASGITransport(app=stub_app), base_url="http://stub.test") as c:
        yield c
    await engine.dispose()


# -- normalizers ----------------------------------------------------------------------------------
def drop_age_seconds(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: drop_age_seconds(v) for k, v in value.items() if k != "ageSeconds"}
    if isinstance(value, list):
        return [drop_age_seconds(v) for v in value]
    return value


def stub_notifications_to_empty(body: Any) -> Any:
    if isinstance(body, dict) and "alert" in body and "notifications" in body:
        return {**body, "notifications": []}
    return body


def _headers(who: Optional[str]) -> Dict[str, str]:
    if who is None:
        return {}
    if who == "service":
        return bearer(service_token())
    return bearer(token_of(who))


async def compare(stub_client, client, who: Optional[str], path: str, params: Optional[dict] = None) -> Tuple[int, Any]:
    """Sends one request to both apps; asserts equal status and equal normalized JSON; returns (status, body)."""
    headers = _headers(who)
    stub = await stub_client.get(f"/api/v1{path}", params=params, headers=headers)
    ours = await client.get(f"/api/v1{path}", params=params, headers=headers)
    expected = drop_age_seconds(stub_notifications_to_empty(stub.json()))
    actual = drop_age_seconds(ours.json())
    assert ours.status_code == stub.status_code, (path, params, ours.json(), stub.json())
    assert actual == expected
    return ours.status_code, actual


# -- the request set ------------------------------------------------------------------------------
WEEK_AGO = iso(utcnow() - timedelta(days=7))
THREE_DAYS_AGO = iso(utcnow() - timedelta(days=3))

C1_OVERVIEW = {"status": "OPEN", "limit": 10, "size": 10, "sort": "createdAt"}  # what C1's AlertClient sends

LIST_REQUESTS: List[Tuple[str, dict]] = [
    # owner (tenant A, from the token)
    ("anh", {}),
    ("anh", {"status": "OPEN"}),
    ("anh", {"status": "CLOSED"}),
    ("anh", {"status": "NEW"}),
    ("anh", {"status": "NEW,RESOLVED"}),
    ("anh", {"status": "open"}),
    ("anh", {"sort": "severity"}),
    ("anh", {"page": 2, "size": 5}),
    ("anh", {"limit": 3}),
    ("anh", C1_OVERVIEW),
    ("anh", {"type": "accident"}),
    ("anh", {"severity": "critical"}),
    ("anh", {"vehicleId": "AV-01"}),
    ("anh", {"from": WEEK_AGO, "to": THREE_DAYS_AGO}),
    ("anh", {"tenantId": 2}),                       # ignored for a tenant role
    # fleet manager (tenant B)
    ("maria", {}),
    ("maria", {"status": "ACKNOWLEDGED"}),
    ("maria", {"status": "RESOLVED"}),
    ("maria", {"status": "FALSE_ALARM"}),
    ("maria", {"status": "CLOSED", "sort": "severity", "size": 100}),
    ("maria", {"page": 3, "size": 10}),
    ("maria", {"page": 99}),                        # past the end: empty items, same total
    ("maria", {"vehicleId": "AV-12", "status": "OPEN"}),
    ("maria", {"type": "SAFETY", "severity": "MEDIUM"}),
    ("maria", {"from": WEEK_AGO}),
    # rider (tenant A, active trip on AV-01)
    ("rider", {}),
    ("rider", {"status": "OPEN"}),
    # staff, tenant as a number and as a code
    ("staff", {"tenantId": 2}),
    ("staff", {"tenantId": "tB", "status": "OPEN"}),
    ("staff", {"tenantId": "tA", "sort": "severity"}),
    ("admin", {"tenantId": 1, "status": "CLOSED"}),
    ("staff", {"tenantId": 99}),                    # an unknown numeric tenant: empty, as in the stub
    # service token (C1's AlertClient)
    ("service", {"tenantId": 1, **C1_OVERVIEW}),
    ("service", {"tenantId": 2, **C1_OVERVIEW}),
    ("service", {"tenantId": "tB", "page": 2, "size": 10}),
    # errors
    ("staff", {}),                                  # 400 without a tenant
    ("service", {}),                                # 400 without a tenant
    ("anh", {"size": 101}),                         # 400 size over 100
    ("anh", {"page": -1}),                          # 400
    ("anh", {"page": 0}),                           # 0 means the default, as in the stub
    ("anh", {"status": "OPENED"}),                  # 400 unknown status, with the allowed list
    ("anh", {"sort": "type"}),                      # 400
    ("anh", {"from": "yesterday"}),                 # 400
    ("anh", {"size": "ten", "page": "x"}),          # 400 schema errors, field by field
    ("staff", {"tenantId": "tZ"}),                  # 400 unknown tenant code
    ("iot", {"tenantId": 1}),                       # 403
    (None, {"tenantId": 1}),                        # 401 no token
]

MAP_REQUESTS: List[Tuple[str, dict]] = [
    ("anh", {}),
    ("anh", {"status": "OPEN"}),
    ("maria", {}),
    ("maria", {"status": "CLOSED"}),
    ("rider", {}),
    ("staff", {"tenantId": "tB"}),
    ("staff", {"tenantId": 1}),
    ("service", {"status": "OPEN", "tenantId": 1}),  # what C1's AlertClient sends
    ("service", {"status": "OPEN", "tenantId": 2}),
    ("staff", {}),                                   # 400 without a tenant
    ("anh", {"status": "BAD"}),                      # 400
    ("iot", {"tenantId": 2}),                        # 403
]

DETAIL_REQUESTS: List[Tuple[str, str, dict]] = [
    ("anh", "20482", {}),
    ("rider", "20482", {}),
    ("staff", "20482", {}),                          # staff without tenantId: any tenant
    ("staff", "20482", {"tenantId": "tA"}),
    ("service", "20482", {"tenantId": 1}),
    ("maria", "20482", {}),                          # 404: another tenant's alert
    ("staff", "20482", {"tenantId": 2}),             # 404: outside the chosen tenant
    ("anh", "99999", {}),                            # 404: unknown
    ("anh", "abc", {}),                              # 400: not a number
    ("iot", "20482", {}),                            # 403
    (None, "20482", {}),                             # 401
]


def _id(case) -> str:
    return "-".join(str(part) for part in case if part not in (None, {})) or "none"


@pytest.mark.parametrize("who,params", LIST_REQUESTS, ids=[_id(c) for c in LIST_REQUESTS])
async def test_list_matches_stub(stub_client, client, who, params):
    await compare(stub_client, client, who, "/alerts", params)


@pytest.mark.parametrize("who,params", MAP_REQUESTS, ids=[_id(c) for c in MAP_REQUESTS])
async def test_map_matches_stub(stub_client, client, who, params):
    await compare(stub_client, client, who, "/alerts/map", params)


@pytest.mark.parametrize("who,alert_id,params", DETAIL_REQUESTS, ids=[_id(c) for c in DETAIL_REQUESTS])
async def test_detail_matches_stub(stub_client, client, who, alert_id, params):
    await compare(stub_client, client, who, f"/alerts/{alert_id}", params)


async def test_detail_of_every_seeded_alert_matches_stub(app, stub_client, client):
    """All seeded alerts, both tenants and every status, seen by staff and by their own tenant's user."""
    async with app.state.session_factory() as session:
        rows = (await session.execute(select(Alert.alert_id, Alert.tenant_id).order_by(Alert.alert_id))).all()
    assert len(rows) == 41
    for alert_id, tenant_id in rows:
        await compare(stub_client, client, "staff", f"/alerts/{alert_id}")
        await compare(stub_client, client, "anh" if tenant_id == 1 else "maria", f"/alerts/{alert_id}")


async def test_golden_set_is_not_trivial(stub_client, client):
    """Guards the comparison itself: the requests above really return data."""
    _, page = await compare(stub_client, client, "maria", "/alerts", {"size": 100})
    assert page["total"] > 20 and len(page["items"]) == page["total"]
    assert {i["status"] for i in page["items"]} == {"NEW", "ACKNOWLEDGED", "RESOLVED", "FALSE_ALARM"}
    _, points = await compare(stub_client, client, "maria", "/alerts/map")
    assert len(points) > 0
    _, detail = await compare(stub_client, client, "anh", "/alerts/20482")
    assert detail["event"]["eventRef"] == "6729b1c4e1f0a2d3c4b5a697" and detail["rule"]["ruleId"] == 2
    assert detail["clip"]["url"] is None and detail["actions"][0]["by"] == "system"
