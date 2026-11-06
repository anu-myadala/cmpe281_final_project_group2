"""Step 3: who may call, and which tenant they see (auth.py, deps.py). One test per rule.

The alert routes arrive in step 4, so these tests mount two probe routes that use the
real guards: GET /probe/alerts (alert_caller) and GET /probe/alert-rules (rules_caller).
Each answers the Caller it resolved. Tokens are signed here with the test secrets.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from c3_service.auth import Caller, rider_vehicles
from c3_service.deps import alert_caller, rules_caller
from c3_service.errors import install_error_handlers
from c3_service.models import VehicleRider
from c3_service.timeutil import utcnow
from conftest import JWT_SECRET, bearer, service_token, token_of, user_token


def _dto(caller: Caller) -> dict:
    return {"role": caller.role, "userId": caller.user_id, "name": caller.name, "tenantId": caller.tenant_id,
            "tenantCode": caller.tenant_code, "isService": caller.is_service, "isStaff": caller.is_staff,
            "riderVehicles": caller.rider_vehicle_codes}


@pytest_asyncio.fixture(scope="module")
async def probe(app):
    """A tiny app on the same databases and settings as the C3 app, with the two guarded probe routes."""
    probe_app = FastAPI()
    for name in ("settings", "session_factory", "mongo", "tenants"):
        setattr(probe_app.state, name, getattr(app.state, name))
    install_error_handlers(probe_app)

    @probe_app.get("/probe/alerts")
    async def alerts(caller: Caller = Depends(alert_caller)):
        return _dto(caller)

    @probe_app.get("/probe/alert-rules")
    async def rules(caller: Caller = Depends(rules_caller)):
        return _dto(caller)

    async with AsyncClient(transport=ASGITransport(app=probe_app), base_url="http://probe.test") as c:
        yield c


# 1. user JWT: HS256 with JWT_SECRET, claims sub, role, tenantId, name
async def test_user_jwt_claims(probe):
    response = await probe.get("/probe/alerts", headers=bearer(token_of("anh")))
    assert response.status_code == 200
    assert response.json() == {"role": "OWNER", "userId": 1001, "name": "Anh Nguyen", "tenantId": 1,
                               "tenantCode": "tA", "isService": False, "isStaff": False, "riderVehicles": None}


# 2. service token: HS256 with SERVICE_TOKEN_SECRET, role SERVICE, staff scope
async def test_service_token_has_staff_scope(probe):
    response = await probe.get("/probe/alerts", params={"tenantId": 1}, headers=bearer(service_token()))
    assert response.status_code == 200
    body = response.json()
    assert (body["role"], body["isService"], body["isStaff"], body["userId"]) == ("SERVICE", True, True, None)
    assert (body["tenantId"], body["tenantCode"], body["name"]) == (1, "tA", "c1-api")


# 3. a token claiming role SERVICE but signed with the user secret is refused
async def test_service_role_signed_with_user_secret_is_rejected(probe):
    forged = service_token(secret=JWT_SECRET)
    response = await probe.get("/probe/alerts", params={"tenantId": 1}, headers=bearer(forged))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


# 4. tenant roles: the tenant comes from the token, the tenantId parameter is ignored
async def test_tenant_role_ignores_tenant_param(probe):
    response = await probe.get("/probe/alerts", params={"tenantId": "tB"}, headers=bearer(token_of("anh")))
    assert response.status_code == 200
    assert (response.json()["tenantId"], response.json()["tenantCode"]) == (1, "tA")


# 5. staff and service callers pass tenantId as a number or as a tenant code
@pytest.mark.parametrize("who", ["staff", "service"])
@pytest.mark.parametrize("param", ["2", "tB"])
async def test_staff_pass_tenant_as_number_or_code(probe, who, param):
    token = service_token() if who == "service" else token_of("staff")
    response = await probe.get("/probe/alerts", params={"tenantId": param}, headers=bearer(token))
    assert response.status_code == 200
    assert (response.json()["tenantId"], response.json()["tenantCode"]) == (2, "tB")


async def test_unknown_tenant_code_is_400(probe):
    response = await probe.get("/probe/alerts", params={"tenantId": "tZ"}, headers=bearer(token_of("staff")))
    assert response.status_code == 400
    assert response.json()["error"] == {"code": "VALIDATION", "message": "Unknown tenant",
                                        "details": {"field": "tenantId"}}


# 6. IOT_ENGINEER: 403 on every alert route (and so on the rules)
@pytest.mark.parametrize("path", ["/probe/alerts", "/probe/alert-rules"])
async def test_iot_engineer_403_on_alert_routes(probe, path):
    response = await probe.get(path, params={"tenantId": 1}, headers=bearer(token_of("iot")))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


# 7. RIDER: only the cars with an active vehicle_rider row (valid_from <= now < valid_to, or valid_to NULL)
async def test_rider_active_window(app, probe):
    # The seed gives rider 1003 an open trip on AV-01 (vehicle_id 1) that started 10 days ago.
    response = await probe.get("/probe/alerts", headers=bearer(token_of("rider")))
    assert response.status_code == 200 and response.json()["riderVehicles"] == ["AV-01"]

    factory = app.state.session_factory
    now = utcnow()
    trip_from, trip_to = now - timedelta(days=30), now - timedelta(days=20)  # an older, closed trip
    async with factory() as session:
        session.add(VehicleRider(vehicle_id=1, user_id=1003, valid_from=trip_from, valid_to=trip_to, tenant_id=1))
        await session.commit()
    try:
        assert await rider_vehicles(factory, 1003, now=trip_from - timedelta(seconds=1)) == []      # before
        assert await rider_vehicles(factory, 1003, now=trip_from) == ["AV-01"]                      # valid_from included
        assert await rider_vehicles(factory, 1003, now=trip_to - timedelta(seconds=1)) == ["AV-01"]  # inside
        assert await rider_vehicles(factory, 1003, now=trip_to) == []                               # valid_to excluded
        assert await rider_vehicles(factory, 1003, now=now + timedelta(days=365)) == ["AV-01"]      # NULL = open
        assert await rider_vehicles(factory, 1001, now=now) == []                                   # not a rider
    finally:
        async with factory() as session:
            await session.execute(delete(VehicleRider).where(VehicleRider.user_id == 1003,
                                                             VehicleRider.valid_from == trip_from))
            await session.commit()


# 8. OWNER and RIDER: 403 on /alert-rules; fleet managers and staff pass
@pytest.mark.parametrize("who,status", [("anh", 403), ("rider", 403), ("maria", 200), ("staff", 200),
                                        ("admin", 200)])
async def test_owner_and_rider_403_on_alert_rules(probe, who, status):
    response = await probe.get("/probe/alert-rules", headers=bearer(token_of(who)))
    assert response.status_code == status


# 9. no token, a token with a wrong signature, or a user token without a numeric sub: 401
@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Basic abc"},
    bearer(user_token(1001, "OWNER", 1, secret="not-the-secret-but-long-enough-for-hs256")),
    bearer(user_token("anh", "OWNER", 1)),
], ids=["none", "not-bearer", "bad-signature", "non-numeric-sub"])
async def test_missing_or_bad_token_is_401(probe, headers):
    response = await probe.get("/probe/alerts", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
