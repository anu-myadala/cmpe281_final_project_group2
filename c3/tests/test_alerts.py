"""Step 4 unit tests of the read API, on the seeded data: paging, the RIDER filter,
404 outside the tenant, 400 without a tenant."""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from c3_service.models import Alert
from conftest import bearer, service_token, token_of

API = "/api/v1"


async def _get(client, who, path, **params):
    headers = bearer(service_token()) if who == "service" else bearer(token_of(who))
    return await client.get(f"{API}{path}", params=params, headers=headers)


# -- paging -----------------------------------------------------------------------------------------
async def test_total_is_counted_before_paging(app, client):
    async with app.state.session_factory() as session:
        expected = (await session.execute(select(func.count()).select_from(Alert).where(Alert.tenant_id == 2))).scalar_one()
    seen = []
    page = 1
    while True:
        body = (await _get(client, "maria", "/alerts", page=page, size=7)).json()
        assert body["total"] == expected and body["page"] == page and body["size"] == 7
        if not body["items"]:
            break
        assert len(body["items"]) <= 7
        seen += [i["alertId"] for i in body["items"]]
        page += 1
    assert len(seen) == expected and len(set(seen)) == expected  # every alert once, no page overlaps


async def test_default_size_is_25(client):
    body = (await _get(client, "maria", "/alerts")).json()
    assert body["size"] == 25 and body["page"] == 1 and len(body["items"]) == min(25, body["total"])


@pytest.mark.parametrize("size,status", [(100, 200), (101, 400), (-1, 400)])
async def test_size_limit(client, size, status):
    response = await _get(client, "maria", "/alerts", size=size)
    assert response.status_code == status
    if status == 400:
        assert response.json()["error"] == {"code": "VALIDATION", "message": "size must be between 1 and 100",
                                            "details": {"field": "size", "max": 100}}


async def test_limit_is_an_alias_of_size(client):
    by_limit = (await _get(client, "maria", "/alerts", limit=3)).json()
    by_size = (await _get(client, "maria", "/alerts", size=3)).json()
    assert by_limit["size"] == 3 and len(by_limit["items"]) == 3
    assert [i["alertId"] for i in by_limit["items"]] == [i["alertId"] for i in by_size["items"]]
    assert (await _get(client, "maria", "/alerts", limit=101)).status_code == 400
    assert (await _get(client, "maria", "/alerts", size=4, limit=9)).json()["size"] == 4  # size wins


# -- the RIDER filter -------------------------------------------------------------------------------
@pytest_asyncio.fixture
async def other_car_alert(app):
    """An open alert of tenant A on AV-02, a car that is not on the rider's trip (AV-01)."""
    async with app.state.session_factory() as session:
        row = Alert(tenant_id=1, vehicle_id="AV-02", alert_type_code="EMERGENCY", rule_id=1, top_class_id=1,
                    severity="HIGH", status="NEW", event_ref="0" * 24, confidence=0.8, vehicle_state="MOVING",
                    model_version="sound-classifier-1.1.0", lat=37.33, lon=-121.88)
        session.add(row)
        await session.commit()
        alert_id = row.alert_id
    yield alert_id
    async with app.state.session_factory() as session:
        await session.execute(delete(Alert).where(Alert.alert_id == alert_id))
        await session.commit()


async def test_rider_sees_only_the_cars_of_the_trip(client, other_car_alert):
    owner = (await _get(client, "anh", "/alerts", size=100)).json()
    rider = (await _get(client, "rider", "/alerts", size=100)).json()
    assert other_car_alert in [i["alertId"] for i in owner["items"]]
    assert other_car_alert not in [i["alertId"] for i in rider["items"]]
    assert {i["vehicleId"] for i in rider["items"]} == {"AV-01"}
    assert rider["total"] == owner["total"] - 1
    assert (await _get(client, "anh", f"/alerts/{other_car_alert}")).status_code == 200
    assert (await _get(client, "rider", f"/alerts/{other_car_alert}")).status_code == 404
    pins = (await _get(client, "rider", "/alerts/map")).json()
    assert other_car_alert not in [p["alertId"] for p in pins]


# -- tenant scope ---------------------------------------------------------------------------------
async def test_another_tenants_alert_is_404(app, client):
    async with app.state.session_factory() as session:
        tenant_b_alert = (await session.execute(select(Alert.alert_id).where(Alert.tenant_id == 2).limit(1))).scalar_one()
    for who, alert_id in (("maria", 20482), ("anh", tenant_b_alert)):
        response = await _get(client, who, f"/alerts/{alert_id}")
        assert response.status_code == 404
        assert response.json() == {"error": {"code": "NOT_FOUND", "message": "Alert not found"}}
    response = await _get(client, "staff", "/alerts/20482", tenantId="tB")  # staff scoped to tenant B
    assert response.status_code == 404
    assert (await _get(client, "staff", "/alerts/20482")).status_code == 200  # staff, no tenant: any tenant


@pytest.mark.parametrize("who", ["staff", "admin", "service"])
@pytest.mark.parametrize("path", ["/alerts", "/alerts/map"])
async def test_no_tenant_is_400(client, who, path):
    response = await _get(client, who, path)
    assert response.status_code == 400
    assert response.json() == {"error": {"code": "VALIDATION", "message": "tenantId is required",
                                         "details": {"field": "tenantId"}}}
