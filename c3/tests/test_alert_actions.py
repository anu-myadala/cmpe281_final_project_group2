"""POST /alerts/{alertId}/actions: concurrency, publish after commit, and the best-effort
side effects (alert.updated and the C1 feedback label). Each test gets its own copy of
the pristine seeded database, so the session's shared database stays untouched."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update

from c3_service.auth import Caller
from c3_service.errors import Conflict
from c3_service.main import create_app
from c3_service.models import Alert, AlertAction
from c3_service.services.alert_actions import AlertActionService
from c3_service.services.c1_client import C1Client
from c3_service.timeutil import utcnow
from conftest import bearer, make_settings, sqlite_url, token_of

C1_BASE = "http://c1.test/api/v1"
EVENT_20482 = "6729b1c4e1f0a2d3c4b5a697"


class RecordingPublisher:
    """Records each event together with the alert's status as a separate session sees it at that moment,
    which proves the event went out after the COMMIT."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.session_factory = None
        self.calls: List[Tuple[str, Dict[str, Any], int, str]] = []

    async def publish(self, topic: str, payload: Dict[str, Any], tenant_id: int) -> None:
        async with self.session_factory() as session:
            committed = (await session.execute(select(Alert.status).where(Alert.alert_id == payload["alertId"]))).scalar_one()
        self.calls.append((topic, payload, tenant_id, committed))
        if self.fail:
            raise RuntimeError("event bus down")


class C1Recorder:
    """httpx transport standing in for C1: records the feedback calls; can answer 500 or fail to connect."""

    def __init__(self, mode: str = "ok") -> None:
        self.mode = mode
        self.calls: List[Dict[str, Any]] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append({"path": request.url.path, "json": json.loads(request.content),
                           "authorization": request.headers.get("authorization")})
        if self.mode == "connect-error":
            raise httpx.ConnectError("C1 unreachable", request=request)
        return httpx.Response(500 if self.mode == "500" else 201, json={})


@pytest_asyncio.fixture
async def make_env(fresh_db_copy, mongo):
    """make_env(publisher_fails=False, c1_mode="ok") -> (client, app, publisher, c1) on a fresh database copy."""
    stack = []

    async def build(publisher_fails: bool = False, c1_mode: str = "ok"):
        publisher, c1 = RecordingPublisher(fail=publisher_fails), C1Recorder(c1_mode)
        app = create_app(make_settings(database_url=sqlite_url(fresh_db_copy()), c1_base_url=C1_BASE), mongo=mongo,
                         publisher=publisher, c1_client=C1Client(C1_BASE, transport=c1.transport))
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        publisher.session_factory = app.state.session_factory
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://c3.test")
        stack.append((client, lifespan))
        return client, app, publisher, c1

    yield build
    for client, lifespan in reversed(stack):
        await client.aclose()
        await lifespan.__aexit__(None, None, None)


async def _act(client, who: str, alert_id: int, action: str, **params):
    return await client.post(f"/api/v1/alerts/{alert_id}/actions", json={"action": action}, params=params,
                             headers=bearer(token_of(who)))


async def _count_actions(app, alert_id: int, action: str) -> int:
    async with app.state.session_factory() as session:
        return int((await session.execute(select(func.count()).select_from(AlertAction).where(
            AlertAction.alert_id == alert_id, AlertAction.action == action))).scalar_one())


# -- concurrency ------------------------------------------------------------------------------------
async def test_two_simultaneous_acknowledges_give_one_200_and_one_409(make_env):
    client, app, publisher, _ = await make_env()
    first, second = await asyncio.gather(_act(client, "anh", 20482, "ACKNOWLEDGE"),
                                         _act(client, "staff", 20482, "ACKNOWLEDGE"))
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    refused = first if first.status_code == 409 else second
    assert refused.json()["error"]["details"] == {"rule": "BR-C3-07", "from": "ACKNOWLEDGED", "to": "ACKNOWLEDGED",
                                                  "allowed": ["RESOLVE", "FALSE_ALARM"]}
    assert await _count_actions(app, 20482, "ACKNOWLEDGE") == 1
    assert [c[0] for c in publisher.calls] == ["alert.updated"]


async def test_guarded_update_refuses_a_status_that_changed_since_it_was_read(make_env):
    """The race the HTTP test can't force: read NEW, another user acknowledges, then our UPDATE ... WHERE status='NEW'."""
    _, app, publisher, c1 = await make_env()
    service = AlertActionService(app.state.session_factory, publisher, C1Client(C1_BASE, transport=c1.transport))
    caller = Caller(role="OWNER", user_id=1001, name="Anh Nguyen", claim_tenant_id=1, tenant_id=1, tenant_code="tA",
                    token=token_of("anh"))
    async with app.state.session_factory() as ours:
        row = await ours.get(Alert, 20482)
        assert row.status == "NEW"
        async with app.state.session_factory() as other:  # the other user wins
            await other.execute(update(Alert).where(Alert.alert_id == 20482).values(status="ACKNOWLEDGED"))
            await other.commit()
        with pytest.raises(Conflict) as refused:
            await service._transition(ours, row, caller, "ACKNOWLEDGE", "NEW", "ACKNOWLEDGED", None, utcnow())
    assert refused.value.details == {"rule": "BR-C3-07", "from": "ACKNOWLEDGED", "to": "ACKNOWLEDGED",
                                     "allowed": ["RESOLVE", "FALSE_ALARM"]}
    assert await _count_actions(app, 20482, "ACKNOWLEDGE") == 0  # the INSERT was rolled back with it
    assert publisher.calls == []


# -- publish after commit ---------------------------------------------------------------------------
async def test_alert_updated_is_published_once_after_the_commit(make_env):
    client, _, publisher, _ = await make_env()
    response = await _act(client, "anh", 20482, "ACKNOWLEDGE")
    assert response.status_code == 200
    assert publisher.calls == [("alert.updated", {"alertId": 20482, "status": "ACKNOWLEDGED", "by": "Anh Nguyen",
                                                  "action": "ACKNOWLEDGE", "vehicleId": "AV-01"}, 1,
                                "ACKNOWLEDGED")]  # last item: what a separate session saw: already committed


async def test_no_event_when_the_action_is_refused(make_env):
    client, _, publisher, _ = await make_env()
    await _act(client, "anh", 20482, "RESOLVE")
    publisher.calls.clear()
    assert (await _act(client, "anh", 20482, "ACKNOWLEDGE")).status_code == 409
    assert publisher.calls == []


# -- best effort: failures don't change the answer ---------------------------------------------------
async def test_a_failing_publisher_does_not_change_the_200(make_env):
    client, app, publisher, _ = await make_env(publisher_fails=True)
    response = await _act(client, "anh", 20482, "ACKNOWLEDGE")
    assert response.status_code == 200 and response.json()["status"] == "ACKNOWLEDGED"
    assert len(publisher.calls) == 1  # it was tried
    async with app.state.session_factory() as session:
        assert (await session.get(Alert, 20482)).status == "ACKNOWLEDGED"  # and the action stayed


@pytest.mark.parametrize("c1_mode", ["500", "connect-error"])
async def test_a_failing_feedback_call_does_not_change_the_200(make_env, c1_mode):
    client, app, _, c1 = await make_env(c1_mode=c1_mode)
    response = await _act(client, "anh", 20482, "RESOLVE")
    assert response.status_code == 200 and response.json()["status"] == "RESOLVED"
    assert len(c1.calls) == 1
    async with app.state.session_factory() as session:
        assert (await session.get(Alert, 20482)).status == "RESOLVED"


# -- the feedback label ------------------------------------------------------------------------------
@pytest.mark.parametrize("action,label", [("RESOLVE", "CONFIRMED"), ("FALSE_ALARM", "FALSE_ALARM")])
async def test_feedback_label_is_posted_with_the_users_token(make_env, action, label):
    client, _, _, c1 = await make_env()
    assert (await _act(client, "anh", 20482, action)).status_code == 200
    assert len(c1.calls) == 1
    call = c1.calls[0]
    assert call["path"] == f"/api/v1/events/{EVENT_20482}/feedback" and call["json"] == {"label": label}
    assert call["authorization"].startswith("Bearer ") and call["authorization"] != ""
    from jwt import decode
    from conftest import JWT_SECRET
    assert decode(call["authorization"][7:], JWT_SECRET, algorithms=["HS256"])["sub"] == "1001"  # Anh's own token


async def test_no_feedback_on_acknowledge(make_env):
    client, _, _, c1 = await make_env()
    assert (await _act(client, "anh", 20482, "ACKNOWLEDGE")).status_code == 200
    assert c1.calls == []
