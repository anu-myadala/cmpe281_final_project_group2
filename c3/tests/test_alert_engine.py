"""AlertEngine and POST /internal/events/{eventRef}/result.

Part 1, pure unit tests named after the design's tables: Table 102 (R1 to R5, one class of a
result), Table 103 (S1 to S3, severity; S4 is refused by RuleService, and the engine's floor
covers it anyway), BR-C3-02 (vehicle state), BR-C3-03 (overrides), BR-C3-04 (the winner).
Part 2, the result call over HTTP on a fresh copy of the seeded data (rules 1-7, the
subscriptions with SAFETY off on AV-12): the scenarios of Section 4.1, BR-C3-05 (repeats)
and BR-C3-12 (one alert per event, repeated and simultaneous results).
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import update

from c3_service.models import AlertAction, AlertRule
from c3_service.services.alert_engine import Candidate, judge_class, pick_winner
from c3_service.services.rule_resolver import RuleView, select_rule, severity_of, state_of
from conftest import JWT_SECRET, bearer, service_token, token_of
from engine_support import AV01, engine_env  # noqa: F401  (fixture)


def rule(rule_id=1, cls="siren", state="ANY", alert_type="EMERGENCY", min_conf=0.60, tenant=None, enabled=True,
         override=None, repeats=1, window=None) -> RuleView:
    return RuleView(rule_id, tenant, alert_type, cls, state, min_conf, override, repeats, window, enabled)


async def _true():
    return True


async def _false():
    return False


def _count(n):
    async def count():
        return n
    return count


async def _must_not_be_called():
    raise AssertionError("evaluated although an earlier condition already decided")


SEED_RULES = [rule(1, "siren", "ANY", "EMERGENCY", 0.60), rule(2, "crash", "MOVING", "ACCIDENT", 0.70),
              rule(3, "glass_break", "MOVING", "ACCIDENT", 0.70), rule(4, "glass_break", "PARKED", "SECURITY", 0.70),
              rule(5, "scream", "PARKED", "SECURITY", 0.60), rule(6, "scream", "MOVING", "EMERGENCY", 0.60),
              rule(7, "honking", "ANY", "SAFETY", 0.60, repeats=2, window=10)]


# =============================================================================================
# Part 1: pure functions
# =============================================================================================
# -- Table 102 --------------------------------------------------------------------------------
@pytest.mark.parametrize("cls,state,reason", [("traffic", "MOVING", "NO_RULE"), ("environmental", "PARKED", "NO_RULE"),
                                              ("dog_bark", "MOVING", "NO_RULE"), ("crash", "PARKED", "STATE_MISMATCH")])
def test_R1_no_enabled_rule_for_class_and_state(cls, state, reason):
    assert select_rule(SEED_RULES, 1, cls, state) == (None, reason)


async def test_R2_below_threshold():
    assert await judge_class(0.69, SEED_RULES[1], _must_not_be_called, _must_not_be_called) == "BELOW_THRESHOLD"


async def test_R3_service_off():
    assert await judge_class(0.91, SEED_RULES[1], _false, _must_not_be_called) == "SERVICE_OFF"


async def test_R4_repeat_pending():
    assert await judge_class(0.80, SEED_RULES[6], _true, _count(1)) == "REPEAT_PENDING"


async def test_R5_candidate():
    assert await judge_class(0.70, SEED_RULES[1], _true, _must_not_be_called) is None  # exactly the minimum passes
    assert await judge_class(0.80, SEED_RULES[6], _true, _count(2)) is None


# -- Table 103 / BR-C3-06 ----------------------------------------------------------------------
def test_S1_no_override_gives_the_type_default():
    assert severity_of(SEED_RULES[1], "CRITICAL") == "CRITICAL" and severity_of(SEED_RULES[6], "MEDIUM") == "MEDIUM"


def test_S2_override_on_safety_or_security():
    assert severity_of(rule(cls="honking", alert_type="SAFETY", override="HIGH"), "MEDIUM") == "HIGH"
    assert severity_of(rule(cls="scream", alert_type="SECURITY", override="LOW"), "HIGH") == "LOW"


def test_S3_high_or_critical_override_on_emergency_or_accident():
    assert severity_of(rule(cls="crash", alert_type="ACCIDENT", override="HIGH"), "CRITICAL") == "HIGH"
    assert severity_of(rule(cls="siren", alert_type="EMERGENCY", override="CRITICAL"), "HIGH") == "CRITICAL"


def test_emergency_and_accident_never_below_high():
    """S4 is refused when a rule is saved (BR-C3-10); the engine still never goes below HIGH."""
    assert severity_of(rule(cls="crash", alert_type="ACCIDENT", override="MEDIUM"), "CRITICAL") == "HIGH"
    assert severity_of(rule(cls="siren", alert_type="EMERGENCY"), "LOW") == "HIGH"


# -- BR-C3-02 ---------------------------------------------------------------------------------
@pytest.mark.parametrize("context,state", [
    ({"ignition": True, "speedKph": 48}, "MOVING"), ({"ignition": True, "speedKph": 5.1}, "MOVING"),
    ({"ignition": True, "speedKph": 5}, "PARKED"), ({"ignition": False, "speedKph": 48}, "PARKED"),
    ({"ignition": True}, "PARKED"), ({}, "PARKED"), (None, "PARKED")])
def test_BR_C3_02_vehicle_state(context, state):
    assert state_of(context) == state


def test_BR_C3_02_rule_for_ANY_matches_both_states():
    assert select_rule(SEED_RULES, 1, "siren", "MOVING")[0].rule_id == 1
    assert select_rule(SEED_RULES, 1, "siren", "PARKED")[0].rule_id == 1


def test_BR_C3_02_state_specific_rule_before_ANY():
    rules = SEED_RULES + [rule(9, "siren", "PARKED", "SECURITY", 0.9)]
    assert select_rule(rules, 1, "siren", "PARKED")[0].rule_id == 9
    assert select_rule(rules, 1, "siren", "MOVING")[0].rule_id == 1


# -- BR-C3-03 ---------------------------------------------------------------------------------
def test_BR_C3_03_tenant_override_replaces_the_default():
    override = rule(20, "honking", "ANY", "SAFETY", 0.75, tenant=2, repeats=2, window=10)
    assert select_rule(SEED_RULES + [override], 2, "honking", "MOVING")[0] == override
    assert select_rule(SEED_RULES + [override], 1, "honking", "MOVING")[0].rule_id == 7  # other tenants keep it


def test_BR_C3_03_disabled_rule_counts_as_no_rule():
    disabled_default = [r if r.rule_id != 7 else rule(7, "honking", "ANY", "SAFETY", enabled=False) for r in SEED_RULES]
    assert select_rule(disabled_default, 1, "honking", "MOVING") == (None, "NO_RULE")
    parked_off = rule(21, "glass_break", "PARKED", "SECURITY", 0.70, tenant=1, enabled=False)  # design example
    assert select_rule(SEED_RULES + [parked_off], 1, "glass_break", "PARKED") == (None, "STATE_MISMATCH")


# -- BR-C3-04 ---------------------------------------------------------------------------------
def test_BR_C3_04_highest_severity_wins_then_higher_confidence():
    crash = Candidate(SEED_RULES[1], "crash", 0.75, "CRITICAL")
    scream = Candidate(SEED_RULES[5], "scream", 0.95, "HIGH")
    siren = Candidate(SEED_RULES[0], "siren", 0.90, "HIGH")
    assert pick_winner([scream, crash]) == crash
    assert pick_winner([scream, siren]) == scream


# =============================================================================================
# Part 2: the result call
# =============================================================================================
async def test_SC1_crash_while_moving_creates_an_accident_alert(engine_env):
    env = await engine_env()
    ref = await env.add_event("tA", "AV-01", [("crash", 0.91), ("glass_break", 0.06), ("traffic", 0.02)], speed=48)
    response = await env.post(ref, "tA", "AV-01", model_version="sound-classifier-1.2.0")
    assert response.status_code == 200
    decision = response.json()["decision"]
    assert decision == {"alert": True, "alertId": decision["alertId"], "alertType": "ACCIDENT", "severity": "CRITICAL",
                        "reason": "ALERT_CREATED"}
    (row,) = await env.alerts_for(ref)
    assert (row.tenant_id, row.vehicle_id, row.alert_type_code, row.rule_id, row.top_class_id, row.severity, row.status) == \
        (1, "AV-01", "ACCIDENT", 2, 2, "CRITICAL", "NEW")
    assert (float(row.confidence), row.vehicle_state, row.model_version, row.count) == \
        (0.91, "MOVING", "sound-classifier-1.2.0", 1)
    assert (row.lat, row.lon) == AV01 and row.acknowledged_by is None and row.closed_at is None
    async with env.app.state.session_factory() as session:
        from sqlalchemy import select
        actions = (await session.execute(select(AlertAction).where(AlertAction.alert_id == row.alert_id))).scalars().all()
    assert [(a.action, a.user_id, a.from_status, a.to_status) for a in actions] == [("CREATE", None, None, "NEW")]
    assert await env.stored_decision(ref) == decision
    assert [c[0] for c in env.publisher.calls] == ["alert.created"]


async def test_BR_C3_12_the_same_result_again_is_a_duplicate(engine_env):
    env = await engine_env()
    ref = await env.add_event("tA", "AV-01", [("crash", 0.91)])
    first = (await env.post(ref)).json()["decision"]
    second = (await env.post(ref)).json()["decision"]
    assert second == {**first, "reason": "DUPLICATE_RESULT"}
    assert len(await env.alerts_for(ref)) == 1 and len(env.publisher.calls) == 1
    assert (await env.stored_decision(ref))["reason"] == "ALERT_CREATED"  # the first decision is kept


async def test_BR_C3_12_two_simultaneous_identical_results_give_one_alert(engine_env):
    env = await engine_env()
    ref = await env.add_event("tB", "AV-10", [("siren", 0.88)])
    first, second = await asyncio.gather(env.post(ref, "tB", "AV-10"), env.post(ref, "tB", "AV-10"))
    reasons = sorted([first.json()["decision"]["reason"], second.json()["decision"]["reason"]])
    assert [first.status_code, second.status_code] == [200, 200]
    assert reasons == ["ALERT_CREATED", "DUPLICATE_RESULT"]
    assert first.json()["decision"]["alertId"] == second.json()["decision"]["alertId"]
    assert len(await env.alerts_for(ref)) == 1 and len(env.publisher.calls) == 1


async def test_BR_C3_12_a_redelivered_first_honking_does_not_alert_again(engine_env):
    """The design choice you made: a stored decision wins, so a redelivery can't re-count the window."""
    env = await engine_env()
    first = await env.add_event("tB", "AV-10", [("honking", 0.80)], at=env.now)
    assert (await env.post(first, "tB", "AV-10")).json()["decision"]["reason"] == "REPEAT_PENDING"
    second = await env.add_event("tB", "AV-10", [("honking", 0.82)], at=env.now + timedelta(seconds=4))
    assert (await env.post(second, "tB", "AV-10")).json()["decision"]["reason"] == "ALERT_CREATED"
    again = (await env.post(first, "tB", "AV-10")).json()["decision"]
    assert again == {"alert": False, "reason": "DUPLICATE_RESULT"}
    assert await env.alerts_for(first) == []


async def test_SC2_honking_on_AV12_with_safety_off_is_service_off(engine_env):
    env = await engine_env()
    ref = await env.add_event("tB", "AV-12", [("honking", 0.80)], speed=30)
    assert (await env.post(ref, "tB", "AV-12")).json()["decision"] == {"alert": False, "reason": "SERVICE_OFF"}
    assert await env.stored_decision(ref) == {"alert": False, "reason": "SERVICE_OFF"}
    assert await env.alerts_for(ref) == [] and env.publisher.calls == []


async def test_SC2_scream_while_parked_is_a_security_alert(engine_env):
    env = await engine_env()
    ref = await env.add_event("tB", "AV-12", [("scream", 0.83)], speed=0, ignition=False)
    decision = (await env.post(ref, "tB", "AV-12")).json()["decision"]
    assert (decision["alertType"], decision["severity"], decision["reason"]) == ("SECURITY", "HIGH", "ALERT_CREATED")
    assert (await env.alerts_for(ref))[0].rule_id == 5


@pytest.mark.parametrize("speed,ignition,alert_type,severity,rule_id", [
    (0, False, "SECURITY", "HIGH", 4), (35, True, "ACCIDENT", "CRITICAL", 3)], ids=["parked", "moving"])
async def test_glass_break_depends_on_the_vehicle_state(engine_env, speed, ignition, alert_type, severity, rule_id):
    env = await engine_env()
    ref = await env.add_event("tA", "AV-01", [("glass_break", 0.85)], speed=speed, ignition=ignition)
    decision = (await env.post(ref)).json()["decision"]
    assert (decision["alertType"], decision["severity"]) == (alert_type, severity)
    assert (await env.alerts_for(ref))[0].rule_id == rule_id


async def test_SC4_tenant_override_of_the_honking_threshold(engine_env):
    env = await engine_env()
    async with env.app.state.session_factory() as session:
        session.add(AlertRule(tenant_id=2, alert_type_code="SAFETY", class_id=5, vehicle_state="ANY", min_confidence=0.75,
                              min_repeats=2, window_s=10, enabled=True, updated_by=1002))
        await session.commit()
    ref = await env.add_event("tB", "AV-10", [("honking", 0.70)])
    assert (await env.post(ref, "tB", "AV-10")).json()["decision"]["reason"] == "BELOW_THRESHOLD"
    other_tenant = await env.add_event("tA", "AV-01", [("honking", 0.70)])  # tenant A keeps the default 0.60
    assert (await env.post(other_tenant)).json()["decision"]["reason"] == "REPEAT_PENDING"


async def test_BR_C3_05_honking_needs_two_events_within_ten_seconds(engine_env):
    env = await engine_env()
    t0 = env.now
    first = await env.add_event("tB", "AV-10", [("honking", 0.80)], at=t0)
    assert (await env.post(first, "tB", "AV-10")).json()["decision"]["reason"] == "REPEAT_PENDING"
    late = await env.add_event("tB", "AV-11", [("honking", 0.80)], at=t0 + timedelta(seconds=5))  # another car
    assert (await env.post(late, "tB", "AV-11")).json()["decision"]["reason"] == "REPEAT_PENDING"
    far = await env.add_event("tB", "AV-10", [("honking", 0.80)], at=t0 + timedelta(seconds=11))  # outside 10 s of t0
    assert (await env.post(far, "tB", "AV-10")).json()["decision"]["reason"] == "REPEAT_PENDING"
    second = await env.add_event("tB", "AV-10", [("honking", 0.85)], at=t0 + timedelta(seconds=20))  # 9 s after `far`
    decision = (await env.post(second, "tB", "AV-10")).json()["decision"]
    assert (decision["alertType"], decision["severity"], decision["reason"]) == ("SAFETY", "MEDIUM", "ALERT_CREATED")


async def test_R1_state_mismatch_crash_while_parked(engine_env):
    env = await engine_env()
    ref = await env.add_event("tA", "AV-01", [("crash", 0.95)], speed=0, ignition=True)
    assert (await env.post(ref)).json()["decision"] == {"alert": False, "reason": "STATE_MISMATCH"}


async def test_R1_state_mismatch_glass_break_parked_with_the_parked_rule_disabled(engine_env):
    env = await engine_env()
    async with env.app.state.session_factory() as session:
        session.add(AlertRule(tenant_id=1, alert_type_code="SECURITY", class_id=3, vehicle_state="PARKED",
                              min_confidence=0.70, min_repeats=1, enabled=False))
        await session.commit()
    ref = await env.add_event("tA", "AV-01", [("glass_break", 0.85)], speed=0, ignition=False)
    assert (await env.post(ref)).json()["decision"] == {"alert": False, "reason": "STATE_MISMATCH"}


async def test_BR_C3_01_04_top_three_and_the_highest_severity_wins(engine_env):
    env = await engine_env()
    ref = await env.add_event("tA", "AV-01", [("scream", 0.95), ("crash", 0.80), ("siren", 0.70)], speed=40)
    decision = (await env.post(ref)).json()["decision"]
    assert (decision["alertType"], decision["severity"]) == ("ACCIDENT", "CRITICAL")  # crash, though less confident
    assert len(await env.alerts_for(ref)) == 1                                         # one alert per event


async def test_BR_C3_04_tie_on_severity_goes_to_the_higher_confidence(engine_env):
    env = await engine_env()
    ref = await env.add_event("tA", "AV-01", [("siren", 0.70), ("scream", 0.90)], speed=40)  # both EMERGENCY / HIGH
    assert (await env.post(ref)).json()["decision"]["alertType"] == "EMERGENCY"
    assert (await env.alerts_for(ref))[0].rule_id == 6 and float((await env.alerts_for(ref))[0].confidence) == 0.90


async def test_BR_C3_01_only_the_top_three_count(engine_env):
    env = await engine_env()
    ref = await env.add_event("tA", "AV-01", [("traffic", 0.99)])
    response = await env.post(ref, top_k=[("traffic", 0.5), ("environmental", 0.3), ("crash", 0.1), ("siren", 0.95)])
    assert response.status_code == 400  # more than 3 classes is refused, never silently cut


async def test_BR_C3_01_traffic_and_environmental_never_alert(engine_env):
    env = await engine_env()
    ref = await env.add_event("tA", "AV-01", [("traffic", 0.99), ("environmental", 0.95)])
    assert (await env.post(ref)).json()["decision"] == {"alert": False, "reason": "NO_RULE"}


async def test_first_failing_class_gives_the_reason(engine_env):
    env = await engine_env()
    ref = await env.add_event("tA", "AV-01", [("crash", 0.65), ("traffic", 0.30)], speed=40)
    assert (await env.post(ref)).json()["decision"]["reason"] == "BELOW_THRESHOLD"  # crash first, not traffic's NO_RULE


@pytest.mark.parametrize("override,alert_type,cls,expected", [
    ("CRITICAL", "SECURITY", 3, "CRITICAL"), ("LOW", "SECURITY", 3, "LOW")], ids=["S2-up", "S2-down"])
async def test_S2_override_is_used_by_the_engine(engine_env, override, alert_type, cls, expected):
    env = await engine_env()
    async with env.app.state.session_factory() as session:
        await session.execute(update(AlertRule).where(AlertRule.rule_id == 4).values(severity_override=override))
        await session.commit()
    ref = await env.add_event("tA", "AV-01", [("glass_break", 0.85)], speed=0, ignition=False)
    assert (await env.post(ref)).json()["decision"]["severity"] == expected


async def test_error_is_recorded_without_an_alert(engine_env):
    env = await engine_env()
    ref = await env.add_event("tA", "AV-01", [("crash", 0.95)])
    response = await env.post(ref, top_k=[], error="clip 404 from C2")
    assert response.json()["decision"] == {"alert": False, "reason": "ERROR_RECORDED"}
    assert await env.stored_decision(ref) == {"alert": False, "reason": "ERROR_RECORDED"}
    assert await env.alerts_for(ref) == []


async def test_empty_top_k_without_error_is_400(engine_env):
    env = await engine_env()
    ref = await env.add_event()
    response = await env.post(ref, top_k=[])
    assert response.status_code == 400 and response.json()["error"]["code"] == "VALIDATION"


@pytest.mark.parametrize("case", ["unknown", "other-tenant", "other-vehicle", "malformed", "unknown-tenant-code"])
async def test_event_not_found_for_that_tenant_and_vehicle_is_404(engine_env, case):
    env = await engine_env()
    ref = await env.add_event("tB", "AV-10", [("siren", 0.9)])
    args = {"unknown": ("0123456789abcdef01234567", "tB", "AV-10"), "other-tenant": (ref, "tA", "AV-10"),
            "other-vehicle": (ref, "tB", "AV-11"), "malformed": ("not-an-id", "tB", "AV-10"),
            "unknown-tenant-code": (ref, "tZ", "AV-10")}[case]
    response = await env.post(*args, top_k=[("siren", 0.9)])
    assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"
    assert await env.alerts_for(ref) == []


@pytest.mark.parametrize("headers,status", [
    ({}, 401), ({"Authorization": "Bearer nonsense"}, 401),
    (bearer(service_token(secret=JWT_SECRET)), 401),     # role SERVICE signed with the user secret
    ("user", 403)], ids=["no-token", "bad-token", "forged-service", "user-token"])
async def test_service_token_only(engine_env, headers, status):
    env = await engine_env()
    ref = await env.add_event()
    response = await env.post(ref, headers=bearer(token_of("admin")) if headers == "user" else headers)
    assert response.status_code == status
    assert await env.alerts_for(ref) == []


async def test_BR_C3_12_the_unique_key_turns_a_racing_insert_into_a_duplicate(engine_env):
    """The race the HTTP test can't force: both requests decided before either stored a decision, so the
    second INSERT hits UNIQUE (event_ref, alert_type_code), rolls back, and answers the first alert."""
    from c3_service.services.alert_engine import AlertEngine
    from c3_service.services.event_store import ResultEvents
    env = await engine_env()
    ref = await env.add_event("tA", "AV-01", [("crash", 0.91)])
    engine = AlertEngine(env.app.state.session_factory, ResultEvents(env.mongo), env.publisher, env.app.state.tenants)
    event = await engine.events.get_for_vehicle(ref, "tA", "AV-01")
    _, best, state = await engine.decide(event, 1, [("crash", 0.91)])
    first = await engine.create_alert(event, 1, best, state, "sound-classifier-1.1.0")
    second = await engine.create_alert(event, 1, best, state, "sound-classifier-1.1.0")
    assert first["reason"] == "ALERT_CREATED"
    assert second == {**first, "reason": "DUPLICATE_RESULT"}
    assert len(await env.alerts_for(ref)) == 1 and len(env.publisher.calls) == 1
