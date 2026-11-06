"""SC4 end to end: a fleet manager's override, saved through PUT /alert-rules, is what the
AlertEngine (step 7) applies, for that tenant only, until it is deleted."""
from __future__ import annotations

from datetime import timedelta

from conftest import bearer, token_of
from engine_support import engine_env  # noqa: F401  (fixture)


async def _reason(env, tenant, vehicle, conf, seconds):
    ref = await env.add_event(tenant, vehicle, [("honking", conf)], at=env.now + timedelta(seconds=seconds))
    return (await env.post(ref, tenant, vehicle)).json()["decision"]["reason"]


async def test_SC4_tenant_b_raises_the_honking_threshold(engine_env):
    env = await engine_env()
    # SAFETY is on for AV-11 (tenant B) and AV-01 (tenant A); the default honking rule is 0.60, 2 within 10 s.
    assert await _reason(env, "tB", "AV-11", 0.70, 0) == "REPEAT_PENDING"
    saved = await env.client.put("/api/v1/alert-rules/7", json={"minConfidence": 0.75}, headers=bearer(token_of("maria")))
    assert saved.status_code == 201
    assert await _reason(env, "tB", "AV-11", 0.70, 100) == "BELOW_THRESHOLD"   # Tenant B: under its 0.75
    assert await _reason(env, "tB", "AV-11", 0.80, 200) == "REPEAT_PENDING"    # still needs 2 within 10 s
    assert await _reason(env, "tA", "AV-01", 0.70, 300) == "REPEAT_PENDING"    # Tenant A: the default 0.60
    removed = await env.client.delete(f"/api/v1/alert-rules/{saved.json()['ruleId']}", headers=bearer(token_of("maria")))
    assert removed.status_code == 204
    assert await _reason(env, "tB", "AV-11", 0.70, 400) == "REPEAT_PENDING"    # the default is back
