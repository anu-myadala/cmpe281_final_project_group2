"""alert.created is published after the COMMIT, a failing publisher can't fail the result
call, and the payload is alert_created_payload() of the new row."""
from __future__ import annotations

from sqlalchemy import select

from c3_service.models import Alert, AlertAction
from c3_service.services.alert_query import alert_row
from c3_service.services.publisher import alert_created_payload
from engine_support import engine_env  # noqa: F401  (fixture)


async def test_published_after_the_commit_with_the_payload_of_the_new_row(engine_env):
    env = await engine_env()
    ref = await env.add_event("tB", "AV-12", [("scream", 0.83)], speed=0, ignition=False)
    decision = (await env.post(ref, "tB", "AV-12")).json()["decision"]
    ((topic, payload, tenant_id, committed),) = env.publisher.calls
    assert (topic, tenant_id) == ("alert.created", 2)
    assert committed == "NEW"  # a separate session already saw the alert when the event went out
    async with env.app.state.session_factory() as session:
        row = await session.get(Alert, decision["alertId"])
        creates = (await session.execute(select(AlertAction).where(AlertAction.alert_id == row.alert_id))).scalars().all()
    assert [a.action for a in creates] == ["CREATE"]
    assert payload == alert_created_payload(alert_row(row, {}))
    assert payload["alert"]["lat"] is not None and payload["alert"]["eventRef"] == ref


async def test_a_failing_publisher_does_not_fail_the_result_call(engine_env):
    env = await engine_env(publisher_fails=True)
    ref = await env.add_event("tA", "AV-01", [("crash", 0.91)])
    response = await env.post(ref)
    assert response.status_code == 200 and response.json()["decision"]["reason"] == "ALERT_CREATED"
    assert len(env.publisher.calls) == 1                              # it was tried
    assert len(await env.alerts_for(ref)) == 1                        # the alert stayed
    assert (await env.stored_decision(ref))["reason"] == "ALERT_CREATED"


async def test_nothing_is_published_without_an_alert(engine_env):
    env = await engine_env()
    ref = await env.add_event("tA", "AV-01", [("traffic", 0.99)])
    await env.post(ref)
    assert env.publisher.calls == []
