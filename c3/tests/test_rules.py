"""PUT and DELETE /alert-rules: BR-C3-09 (values), BR-C3-10 (who edits what, and the EMERGENCY /
ACCIDENT floor), every role, overrides in the effective list, and the audit rows. Each test runs
on a fresh copy of the seeded data (rules 1-7 as in Table 95)."""
from __future__ import annotations

from typing import Optional

import pytest
from sqlalchemy import select

from c3_service.auth import Caller
from c3_service.errors import BusinessRule, Forbidden, NotFound, Validation
from c3_service.models import AlertRule, AuditLog
from c3_service.services.rule_service import check_permission, check_safety, check_values
from conftest import bearer, service_token, token_of
from engine_support import engine_env  # noqa: F401  (fixture)

HONKING, CRASH, SIREN = 7, 2, 1   # seeded defaults: SAFETY, ACCIDENT, EMERGENCY


def _caller(role: str, tenant_id: Optional[int] = None, service: bool = False) -> Caller:
    return Caller(role=role, user_id=None if service else 1, name=role, claim_tenant_id=None, tenant_id=tenant_id,
                  tenant_code=None, token="t", is_service=service)


async def _put(env, who, rule_id, body, **params):
    headers = bearer(service_token()) if who == "service" else bearer(token_of(who))
    return await env.client.put(f"/api/v1/alert-rules/{rule_id}", json=body, params=params, headers=headers)


async def _delete(env, who, rule_id, **params):
    headers = bearer(service_token()) if who == "service" else bearer(token_of(who))
    return await env.client.delete(f"/api/v1/alert-rules/{rule_id}", params=params, headers=headers)


async def _list(env, who, **params):
    return (await env.client.get("/api/v1/alert-rules", params=params, headers=bearer(token_of(who)))).json()


async def _audit(env):
    async with env.app.state.session_factory() as session:
        return (await session.execute(select(AuditLog).order_by(AuditLog.audit_id))).scalars().all()


# =============================================================================================
# BR-C3-09, pure
# =============================================================================================
@pytest.mark.parametrize("conf,repeats,window,ok", [
    (0.40, 1, None, True), (0.99, 1, None, True), (0.39, 1, None, False), (1.00, 1, None, False),
    (0.6, 1, None, True), (0.6, 5, 60, True), (0.6, 0, None, False), (0.6, 6, 10, False),
    (0.6, 2, 5, True), (0.6, 2, 4, False), (0.6, 2, 61, False), (0.6, 2, None, False)])
def test_BR_C3_09_value_bounds(conf, repeats, window, ok):
    if ok:
        check_values(conf, repeats, window)
    else:
        with pytest.raises(BusinessRule) as refused:
            check_values(conf, repeats, window)
        assert refused.value.details["rule"] == "BR-C3-09" and refused.value.status == 422


# =============================================================================================
# BR-C3-10, pure
# =============================================================================================
@pytest.mark.parametrize("alert_type,is_override,enabled,severity,ok", [
    ("ACCIDENT", True, True, "MEDIUM", False), ("EMERGENCY", False, True, "LOW", False),   # never below HIGH
    ("ACCIDENT", True, True, "HIGH", True), ("EMERGENCY", True, True, "CRITICAL", True),
    ("EMERGENCY", True, False, None, False), ("ACCIDENT", True, False, None, False),       # a tenant can't disable
    ("EMERGENCY", False, False, None, True),                                                # SYS_ADMIN on a default
    ("SAFETY", True, False, "LOW", True), ("SECURITY", True, True, "LOW", True)])
def test_BR_C3_10_emergency_and_accident_floor(alert_type, is_override, enabled, severity, ok):
    if ok:
        check_safety(alert_type, is_override, enabled, severity)
    else:
        with pytest.raises(BusinessRule) as refused:
            check_safety(alert_type, is_override, enabled, severity)
        assert refused.value.details["rule"] == "BR-C3-10"


@pytest.mark.parametrize("caller,rule_tenant,outcome", [
    (_caller("FLEET_MANAGER", 2), None, 2),            # a default -> its own override
    (_caller("FLEET_MANAGER", 2), 2, 2),               # its own override
    (_caller("FLEET_MANAGER", 2), 1, NotFound),        # another tenant's override
    (_caller("SERVICE_STAFF", 1), None, 1),
    (_caller("SERVICE_STAFF", 1), 2, NotFound),
    (_caller("SERVICE_STAFF"), None, Validation),      # tenantId required
    (_caller("SERVICE_STAFF"), 2, Validation),
    (_caller("SYS_ADMIN"), None, None),                # the default itself
    (_caller("SYS_ADMIN"), 2, 2),                      # any tenant's override
    (_caller("SYS_ADMIN", 1), None, 1),
    (_caller("SERVICE", 1, service=True), None, Forbidden),
    (_caller("OWNER", 1), None, Forbidden), (_caller("RIDER", 1), None, Forbidden),
    (_caller("IOT_ENGINEER"), None, Forbidden)])
def test_BR_C3_10_who_edits_what(caller, rule_tenant, outcome):
    if isinstance(outcome, type) and issubclass(outcome, Exception):
        with pytest.raises(outcome):
            check_permission(caller, rule_tenant)
    else:
        assert check_permission(caller, rule_tenant) == outcome


# =============================================================================================
# PUT over HTTP
# =============================================================================================
async def test_fleet_manager_put_on_a_default_creates_the_override_then_updates_it(engine_env):
    env = await engine_env()
    first = await _put(env, "maria", HONKING, {"minConfidence": 0.75})
    assert first.status_code == 201
    override = first.json()
    assert (override["scope"], override["tenantId"], override["minConfidence"], override["updatedBy"]) == \
        ("TENANT", 2, 0.75, "Maria Lopez")
    assert override["ruleId"] > 7 and override["windowSeconds"] == 10 and override["minRepeats"] == 2  # copied
    again = await _put(env, "maria", HONKING, {"minConfidence": 0.80})       # PUT on the default again
    assert again.status_code == 200 and again.json()["ruleId"] == override["ruleId"]
    direct = await _put(env, "maria", override["ruleId"], {"minRepeats": 3})  # PUT on the override itself
    assert direct.status_code == 200 and (direct.json()["minConfidence"], direct.json()["minRepeats"]) == (0.8, 3)


async def test_the_effective_list_shows_the_override_in_place_of_its_default(engine_env):
    env = await engine_env()
    created = (await _put(env, "maria", HONKING, {"minConfidence": 0.75, "severityOverride": "HIGH"})).json()
    tenant_b = await _list(env, "maria")
    assert [r["ruleId"] for r in tenant_b] == [1, 2, 3, 4, 5, 6, created["ruleId"]]
    assert (tenant_b[6]["severity"], tenant_b[6]["severityOverride"], tenant_b[6]["scope"]) == ("HIGH", "HIGH", "TENANT")
    assert [r["ruleId"] for r in await _list(env, "staff", tenantId="tA")] == [1, 2, 3, 4, 5, 6, 7]  # tenant A unchanged
    assert [r["ruleId"] for r in await _list(env, "staff")] == [1, 2, 3, 4, 5, 6, 7]                 # the defaults


@pytest.mark.parametrize("who,params,status", [
    ("staff", {"tenantId": "tB"}, 201), ("staff", {"tenantId": 1}, 201), ("admin", {"tenantId": 2}, 201),
    ("admin", {}, 200),                      # SYS_ADMIN edits the default itself
    ("staff", {}, 400),                      # tenantId required
    ("anh", {}, 403), ("rider", {}, 403), ("iot", {"tenantId": 1}, 403), ("service", {"tenantId": 1}, 403)])
async def test_every_role_on_put(engine_env, who, params, status):
    env = await engine_env()
    response = await _put(env, who, HONKING, {"minConfidence": 0.7}, **params)
    assert response.status_code == status, response.json()
    if who == "admin" and not params:
        body = response.json()
        assert (body["ruleId"], body["scope"], body["minConfidence"], body["updatedBy"]) == (7, "DEFAULT", 0.7,
                                                                                            "Platform Admin")


async def test_another_tenants_override_is_404_and_unknown_rule_is_404(engine_env):
    env = await engine_env()
    tenant_a = (await _put(env, "admin", HONKING, {"minConfidence": 0.7}, tenantId=1)).json()["ruleId"]
    assert (await _put(env, "maria", tenant_a, {"minConfidence": 0.9})).status_code == 404
    assert (await _put(env, "staff", tenant_a, {"minConfidence": 0.9}, tenantId=2)).status_code == 404
    assert (await _put(env, "maria", 999, {"minConfidence": 0.9})).status_code == 404


@pytest.mark.parametrize("who,rule_id,body,params,rule", [
    ("maria", HONKING, {"minConfidence": 0.39}, {}, "BR-C3-09"),
    ("maria", HONKING, {"minConfidence": 1.0}, {}, "BR-C3-09"),
    ("maria", HONKING, {"minRepeats": 6}, {}, "BR-C3-09"),
    ("maria", HONKING, {"windowSeconds": 61}, {}, "BR-C3-09"),
    ("maria", CRASH, {"minRepeats": 2}, {}, "BR-C3-09"),               # repeats without a window
    ("maria", CRASH, {"severityOverride": "MEDIUM"}, {}, "BR-C3-10"),  # ACCIDENT below HIGH
    ("maria", SIREN, {"enabled": False}, {}, "BR-C3-10"),             # a tenant disabling EMERGENCY
    ("admin", CRASH, {"severityOverride": "LOW"}, {}, "BR-C3-10"),     # not even on a default
])
async def test_refused_values_are_422_with_the_rule_and_change_nothing(engine_env, who, rule_id, body, params, rule):
    env = await engine_env()
    response = await _put(env, who, rule_id, body, **params)
    assert response.status_code == 422, response.json()
    assert response.json()["error"]["code"] == "BUSINESS_RULE" and response.json()["error"]["details"]["rule"] == rule
    async with env.app.state.session_factory() as session:
        assert (await session.execute(select(AlertRule).where(AlertRule.tenant_id.is_not(None)))).scalars().all() == []
    assert await _audit(env) == []


async def test_allowed_edits_under_BR_C3_10(engine_env):
    env = await engine_env()
    assert (await _put(env, "admin", SIREN, {"enabled": False})).status_code == 200          # SYS_ADMIN, a default
    assert (await _put(env, "maria", HONKING, {"enabled": False})).status_code == 201         # SAFETY may be disabled
    raised = await _put(env, "maria", CRASH, {"severityOverride": "HIGH"})                    # ACCIDENT at HIGH
    assert raised.status_code == 201 and raised.json()["severity"] == "HIGH"
    cleared = await _put(env, "maria", raised.json()["ruleId"], {"severityOverride": None})   # null clears it
    assert cleared.status_code == 200 and (cleared.json()["severityOverride"], cleared.json()["severity"]) == (None,
                                                                                                         "CRITICAL")


async def test_bad_bodies_are_400(engine_env):
    env = await engine_env()
    assert (await _put(env, "maria", HONKING, {"minConfidense": 0.7})).status_code == 400   # unknown field
    assert (await _put(env, "maria", HONKING, {"severityOverride": "URGENT"})).status_code == 400
    assert (await _put(env, "maria", HONKING, {"minConfidence": None})).status_code == 400


async def test_post_is_not_built(engine_env):
    env = await engine_env()
    response = await env.client.post("/api/v1/alert-rules", json={}, headers=bearer(token_of("maria")))
    assert response.status_code == 405


# =============================================================================================
# DELETE over HTTP
# =============================================================================================
async def test_delete_restores_the_default(engine_env):
    env = await engine_env()
    override = (await _put(env, "maria", HONKING, {"minConfidence": 0.75})).json()["ruleId"]
    assert (await _delete(env, "maria", override)).status_code == 204
    assert [r["ruleId"] for r in await _list(env, "maria")] == [1, 2, 3, 4, 5, 6, 7]
    assert (await _delete(env, "maria", override)).status_code == 404     # gone


@pytest.mark.parametrize("who,params,status", [
    ("maria", {}, 204), ("staff", {"tenantId": 2}, 204), ("admin", {}, 204), ("admin", {"tenantId": "tB"}, 204),
    ("staff", {"tenantId": 1}, 404),                    # outside the chosen tenant
    ("staff", {}, 400), ("anh", {}, 403), ("rider", {}, 403), ("service", {"tenantId": 2}, 403)])
async def test_every_role_on_delete(engine_env, who, params, status):
    env = await engine_env()
    override = (await _put(env, "maria", HONKING, {"minConfidence": 0.75})).json()["ruleId"]
    assert (await _delete(env, who, override, **params)).status_code == status


async def test_a_default_cannot_be_deleted_and_unknown_is_404(engine_env):
    env = await engine_env()
    for who, params in (("admin", {}), ("maria", {})):
        response = await _delete(env, who, HONKING, **params)
        assert response.status_code == 409 and response.json()["error"]["code"] == "CONFLICT"
    assert (await _delete(env, "admin", 999)).status_code == 404


# =============================================================================================
# The audit rows (C1's audit_log)
# =============================================================================================
async def test_every_save_and_delete_is_audited_in_c1s_audit_log(engine_env):
    env = await engine_env()
    created = (await env.client.put(f"/api/v1/alert-rules/{HONKING}", json={"minConfidence": 0.75},
                                    headers={**bearer(token_of("maria")), "X-Forwarded-For": "203.0.113.7, 10.0.0.2"})).json()
    await _put(env, "maria", created["ruleId"], {"minConfidence": 0.8})
    await _delete(env, "maria", created["ruleId"])
    rows = await _audit(env)
    assert [(r.action, r.entity, r.entity_id, r.actor_user_id, r.tenant_id) for r in rows] == [
        ("RULE_SAVE", "alert_rule", str(created["ruleId"]), 1002, 2)] * 2 + [
        ("RULE_DELETE", "alert_rule", str(created["ruleId"]), 1002, 2)]
    insert, update, delete = rows
    assert insert.before_json is None and insert.after_json["min_confidence"] == 0.75 and insert.ip == "203.0.113.7"
    assert update.before_json["min_confidence"] == 0.75 and update.after_json["min_confidence"] == 0.8
    assert delete.before_json["min_confidence"] == 0.8 and delete.after_json is None
