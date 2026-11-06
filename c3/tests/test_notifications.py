"""Notifications of a new alert, through the result call on a fresh copy of the seeded data:
the rows (SC1, SC2, N4 with the rider window), the emails sent after the response with their
three attempts (BR-C3-11), the alert detail listing the real rows, and the email itself."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import List, Optional

import pytest
from sqlalchemy import delete, select, update

from c3_service.config import Settings
from c3_service.models import Alert, Notification, Subscription, VehicleRider
from c3_service.services.alert_mailer import AlertMailer
from conftest import bearer, token_of
from engine_support import engine_env  # noqa: F401  (fixture)

NIGHT_LA = datetime(2026, 11, 7, 7, 10)   # 23:10 in Los Angeles (PST)
NOON_LA = datetime(2026, 11, 6, 20, 0)    # 12:00 in Los Angeles


class FakeMailer:
    """Plays the mailer: each send takes the next outcome of `script` ("ok" or "fail", the last one repeats);
    with `gate`, every send waits until the test opens it."""

    def __init__(self, *script: str, gate: Optional[asyncio.Event] = None) -> None:
        self.script = list(script) or ["ok"]
        self.gate = gate
        self.calls: List[tuple] = []

    async def send(self, alert, recipient, time_zone="UTC"):
        self.calls.append((alert.alert_id, recipient, time_zone))
        if self.gate is not None:
            await self.gate.wait()
        if self.script[min(len(self.calls), len(self.script)) - 1] == "fail":
            raise RuntimeError(f"SMTP refused attempt {len(self.calls)}")


async def _rows(env, alert_id: int) -> List[tuple]:
    async with env.app.state.session_factory() as session:
        rows = (await session.execute(select(Notification).where(Notification.alert_id == alert_id)
                                      .order_by(Notification.notification_id))).scalars().all()
    return [(n.channel, n.recipient_user_id, n.status, n.attempts, n.last_error, n.sent_at is not None) for n in rows]


async def _alert(env, alert_id: int) -> Alert:
    async with env.app.state.session_factory() as session:
        return await session.get(Alert, alert_id)


async def _crash_on_av01(env) -> int:
    ref = await env.add_event("tA", "AV-01", [("crash", 0.91)], speed=48)
    response = await env.post(ref)
    assert response.status_code == 200
    return response.json()["decision"]["alertId"]


# -- the rows: SC1, SC2, N4 -------------------------------------------------------------------------
async def test_SC1_crash_dashboard_sent_and_an_email_for_anh(engine_env):
    mailer = FakeMailer("ok")
    env = await engine_env(mailer=mailer, clock=lambda: NOON_LA)
    alert_id = await _crash_on_av01(env)
    await env.app.state.notifier.drain()
    assert await _rows(env, alert_id) == [("DASHBOARD", None, "SENT", 0, None, True),
                                          ("EMAIL", 1001, "SENT", 1, None, True)]
    assert mailer.calls == [(alert_id, "anh@example.com", "America/Los_Angeles")]


async def test_SC2_scream_parked_at_23_10_inside_quiet_hours_is_dashboard_only(engine_env):
    mailer = FakeMailer("ok")
    env = await engine_env(mailer=mailer, clock=lambda: NIGHT_LA)
    ref = await env.add_event("tB", "AV-12", [("scream", 0.83)], speed=0, ignition=False)
    decision = (await env.post(ref, "tB", "AV-12")).json()["decision"]
    assert (decision["alertType"], decision["severity"], decision["reason"]) == ("SECURITY", "HIGH", "ALERT_CREATED")
    await env.app.state.notifier.drain()
    assert await _rows(env, decision["alertId"]) == [("DASHBOARD", None, "SENT", 0, None, True)]
    assert mailer.calls == []


async def test_SC2_the_same_scream_at_noon_emails_the_fleet_manager(engine_env):
    env = await engine_env(mailer=FakeMailer("ok"), clock=lambda: NOON_LA)
    ref = await env.add_event("tB", "AV-12", [("scream", 0.83)], speed=0, ignition=False)
    alert_id = (await env.post(ref, "tB", "AV-12")).json()["decision"]["alertId"]
    await env.app.state.notifier.drain()
    assert [(c, u) for c, u, *_ in await _rows(env, alert_id)] == [("DASHBOARD", None), ("EMAIL", 1002)]  # Maria


@pytest.mark.parametrize("trip,emailed", [
    ("active", [1001, 1003]),          # N4: notify_riders on and Rider Demo on an active trip of AV-01
    ("ended", [1001]),                 # the trip ended an hour ago: N3
    ("starts-later", [1001])], ids=["N4-active-rider", "N3-trip-ended", "N3-trip-not-started"])
async def test_N4_riders_are_emailed_only_during_their_trip(engine_env, trip, emailed):
    env = await engine_env(mailer=FakeMailer("ok"), clock=lambda: NOON_LA)
    async with env.app.state.session_factory() as session:
        await session.execute(update(Subscription).where(Subscription.vehicle_id == 1).values(notify_riders=True))
        if trip != "active":  # replace the seeded open trip of rider 1003 on AV-01
            await session.execute(delete(VehicleRider).where(VehicleRider.user_id == 1003))
            start, end = ((NOON_LA - timedelta(hours=3), NOON_LA - timedelta(hours=1)) if trip == "ended"
                          else (NOON_LA + timedelta(hours=1), None))
            session.add(VehicleRider(vehicle_id=1, user_id=1003, valid_from=start, valid_to=end, tenant_id=1))
        await session.commit()
    alert_id = await _crash_on_av01(env)
    await env.app.state.notifier.drain()
    assert [u for c, u, *_ in await _rows(env, alert_id) if c == "EMAIL"] == emailed


async def test_N1_without_the_email_channel_dashboard_only(engine_env):
    env = await engine_env(mailer=FakeMailer("ok"), clock=lambda: NOON_LA)
    async with env.app.state.session_factory() as session:
        await session.execute(update(Subscription).where(Subscription.vehicle_id == 1).values(channels="DASHBOARD"))
        await session.commit()
    alert_id = await _crash_on_av01(env)
    assert [c for c, *_ in await _rows(env, alert_id)] == ["DASHBOARD"]


# -- the emails: BR-C3-11 -----------------------------------------------------------------------------
async def test_two_failures_then_success_is_sent_with_three_attempts(engine_env):
    env = await engine_env(mailer=FakeMailer("fail", "fail", "ok"), clock=lambda: NOON_LA)
    alert_id = await _crash_on_av01(env)
    await env.app.state.notifier.drain()
    (email,) = [r for r in await _rows(env, alert_id) if r[0] == "EMAIL"]
    assert email == ("EMAIL", 1001, "SENT", 3, "SMTP refused attempt 2", True)  # last_error keeps the last failure


async def test_three_failures_mark_failed_and_leave_the_alert_alone(engine_env):
    env = await engine_env(mailer=FakeMailer("fail"), clock=lambda: NOON_LA)
    alert_id = await _crash_on_av01(env)
    before = await _alert(env, alert_id)
    await env.app.state.notifier.drain()
    (email,) = [r for r in await _rows(env, alert_id) if r[0] == "EMAIL"]
    assert email == ("EMAIL", 1001, "FAILED", 3, "SMTP refused attempt 3", False)
    after = await _alert(env, alert_id)
    assert (after.status, after.updated_at, after.acknowledged_at) == (before.status, before.updated_at, None)


async def test_the_result_call_does_not_wait_for_the_email(engine_env):
    gate = asyncio.Event()
    mailer = FakeMailer("ok", gate=gate)
    env = await engine_env(mailer=mailer, clock=lambda: NOON_LA)
    alert_id = await _crash_on_av01(env)                     # the response is here ...
    assert [r[2] for r in await _rows(env, alert_id)] == ["SENT", "PENDING"]  # ... while the email is pending
    for _ in range(200):                                      # the background task reads the rows, then sends
        if mailer.calls:
            break
        await asyncio.sleep(0.01)
    assert len(mailer.calls) == 1                             # the send has started and is blocked on the gate
    assert [r[2] for r in await _rows(env, alert_id)] == ["SENT", "PENDING"]
    gate.set()
    await env.app.state.notifier.drain()
    assert [r[2] for r in await _rows(env, alert_id)] == ["SENT", "SENT"]


async def test_a_raising_mailer_never_fails_the_result_call(engine_env):
    class Broken:
        async def send(self, *args, **kwargs):
            raise ConnectionRefusedError("mailhog is down")
    env = await engine_env(mailer=Broken(), clock=lambda: NOON_LA)
    alert_id = await _crash_on_av01(env)
    await env.app.state.notifier.drain()
    assert [r[2] for r in await _rows(env, alert_id)] == ["SENT", "FAILED"]
    assert (await _alert(env, alert_id)).status == "NEW"


async def test_a_failure_while_recording_the_rows_never_fails_the_result_call(engine_env, monkeypatch):
    from c3_service.services import notification_service

    async def broken(self, alert, now):
        raise RuntimeError("database hiccup")
    monkeypatch.setattr(notification_service.RecipientResolver, "for_alert", broken)
    env = await engine_env(mailer=FakeMailer("ok"))
    alert_id = await _crash_on_av01(env)
    assert await _rows(env, alert_id) == [] and (await _alert(env, alert_id)).status == "NEW"


# -- the alert page lists the real rows ------------------------------------------------------------------
async def test_alert_detail_lists_the_real_rows_of_an_alert_from_the_result_call(engine_env):
    env = await engine_env(mailer=FakeMailer("ok"), clock=lambda: NOON_LA)
    alert_id = await _crash_on_av01(env)
    await env.app.state.notifier.drain()
    detail = (await env.client.get(f"/api/v1/alerts/{alert_id}", headers=bearer(token_of("anh")))).json()
    notifications = detail["notifications"]
    assert [(n["recipient"], n["channel"], n["status"]) for n in notifications] == [
        ("dashboard", "DASHBOARD", "SENT"), ("anh@example.com", "EMAIL", "SENT")]
    assert all(n["sentAt"].endswith("Z") for n in notifications) and set(notifications[0]) == {
        "recipient", "channel", "status", "sentAt"}


# -- the email ----------------------------------------------------------------------------------------
def test_email_subject_body_and_link():
    mailer = AlertMailer(Settings(_env_file=None, public_base_url="http://scicp.example:8080/", mail_from="a@b.example"))
    alert = Alert(alert_id=30001, tenant_id=1, vehicle_id="AV-01", alert_type_code="ACCIDENT", top_class_id=2,
                  severity="CRITICAL", confidence=0.91, created_at=datetime(2026, 11, 7, 7, 10, 5))
    message = mailer.build(alert, "anh@example.com", "America/Los_Angeles")
    assert message["Subject"] == "[SCICP] ACCIDENT alert on AV-01: crash (CRITICAL)"
    assert (message["From"], message["To"]) == ("a@b.example", "anh@example.com")
    text = message.get_body(("plain",)).get_content()
    html = message.get_body(("html",)).get_content()
    for fact in ("Vehicle: AV-01", "Alert type: ACCIDENT", "Severity: CRITICAL", "Sound class: crash",
                 "Confidence: 0.91", "Time: 2026-11-06 23:10:05 PST (America/Los_Angeles)"):
        assert fact in text
    assert "Open the alert: http://scicp.example:8080/alerts/30001" in text
    assert 'href="http://scicp.example:8080/alerts/30001"' in html and "<b>AV-01</b>" in html
