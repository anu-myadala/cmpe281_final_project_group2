"""BR-04 subscription decision table (Part I, Table 27): one test per column
R1..R5, plus the default rows of a new vehicle and the internal rows C3 reads."""
from __future__ import annotations

from sqlalchemy import select

from app.models import AuditLog, Vehicle


def _service(code, enabled=True, channels=("DASHBOARD", "EMAIL"), quiet_from=None, quiet_to=None):
    return {"serviceCode": code, "enabled": enabled, "channels": list(channels), "quietFrom": quiet_from,
            "quietTo": quiet_to}


async def test_r1_design_only_service_cannot_be_enabled(client, auth):
    response = await client.put("/api/v1/subscriptions/AV-01", headers=await auth.headers("anh"),
                                json={"services": [_service("PASSENGER", True)], "notifyRiders": False})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "BUSINESS_RULE" and error["details"]["rule"] == "BR-04"
    assert error["details"]["field"] == "PASSENGER.enabled"


async def test_r1_design_only_service_may_be_saved_disabled(client, auth):
    response = await client.put("/api/v1/subscriptions/AV-01", headers=await auth.headers("anh"),
                                json={"services": [_service("PASSENGER", False, ["DASHBOARD"])], "notifyRiders": False})
    assert response.status_code == 200
    passenger = next(s for s in response.json()["services"] if s["serviceCode"] == "PASSENGER")
    assert passenger["enabled"] is False


async def test_r2_channels_must_be_allowed_by_plan(client, auth):
    # Tenant A is on the BASIC plan: SMS is not allowed.
    response = await client.put("/api/v1/subscriptions/AV-01", headers=await auth.headers("anh"),
                                json={"services": [_service("SECURITY", True, ["DASHBOARD", "EMAIL", "SMS"])],
                                      "notifyRiders": False})
    assert response.status_code == 422
    details = response.json()["error"]["details"]
    assert details["rule"] == "BR-04" and details["field"] == "SECURITY.channels"
    assert details["allowed"] == ["DASHBOARD", "EMAIL"]


async def test_r3_quiet_hours_need_both_times_and_must_differ(client, auth):
    headers = await auth.headers("maria")
    one_side = await client.put("/api/v1/subscriptions/AV-12", headers=headers,
                                json={"services": [_service("SECURITY", quiet_from="22:00")], "notifyRiders": True})
    assert one_side.status_code == 422
    assert one_side.json()["error"]["details"]["field"] == "SECURITY.quietHours"
    equal = await client.put("/api/v1/subscriptions/AV-12", headers=headers,
                             json={"services": [_service("SECURITY", quiet_from="22:00", quiet_to="22:00")],
                                   "notifyRiders": True})
    assert equal.status_code == 422
    assert equal.json()["error"]["details"]["field"] == "SECURITY.quietHours"


async def test_r4_dashboard_is_always_added(client, auth):
    response = await client.put("/api/v1/subscriptions/AV-01", headers=await auth.headers("anh"),
                                json={"services": [_service("SAFETY", True, ["EMAIL"])], "notifyRiders": False})
    assert response.status_code == 200
    safety = next(s for s in response.json()["services"] if s["serviceCode"] == "SAFETY")
    assert safety["channels"] == ["DASHBOARD", "EMAIL"]


async def test_r5_valid_card_is_saved_and_audited(client, auth, db, service_headers):
    headers = await auth.headers("maria")  # FLEET plan: SMS and PUSH allowed
    body = {"services": [_service("SECURITY", True, ["DASHBOARD", "EMAIL", "SMS"], "22:00", "06:00"),
                         _service("SAFETY", False, ["DASHBOARD"])],
            "notifyRiders": True}
    response = await client.put("/api/v1/subscriptions/AV-12", headers=headers, json=body)
    assert response.status_code == 200, response.text
    card = response.json()
    assert card["vehicleId"] == "AV-12" and card["notifyRiders"] is True
    security = next(s for s in card["services"] if s["serviceCode"] == "SECURITY")
    assert security["channels"] == ["DASHBOARD", "EMAIL", "SMS"]
    assert security["quietFrom"] == "22:00" and security["quietTo"] == "06:00"
    assert next(s for s in card["services"] if s["serviceCode"] == "SAFETY")["enabled"] is False
    # GET returns the same card.
    listed = await client.get("/api/v1/subscriptions?vehicleId=AV-12", headers=headers)
    assert listed.status_code == 200 and listed.json()[0]["notifyRiders"] is True
    # Audit row with before and after.
    rows = (await db.execute(select(AuditLog).where(AuditLog.action == "SUBSCRIPTION_SAVE",
                                                    AuditLog.entity_id == "AV-12"))).scalars().all()
    assert rows and rows[-1].before_json is not None and rows[-1].after_json is not None
    # The rows C3 reads carry the time zone and the quiet hours.
    internal = await client.get("/api/v1/internal/subscriptions?tenantId=2&vehicleId=AV-12", headers=service_headers)
    assert internal.status_code == 200
    sec = next(r for r in internal.json() if r["serviceCode"] == "SECURITY")
    assert sec["timeZone"] == "America/Los_Angeles" and sec["quietFrom"] == "22:00" and sec["notifyRiders"] is True


async def test_list_all_vehicles_of_scope(client, auth):
    response = await client.get("/api/v1/subscriptions", headers=await auth.headers("maria"))
    assert response.status_code == 200
    codes = [c["vehicleId"] for c in response.json()]
    assert codes == ["AV-10", "AV-11", "AV-12", "AV-13", "AV-14"]
    assert len(response.json()[0]["services"]) == 6


async def test_staff_read_only(client, auth):
    staff = await auth.headers("staff")
    assert (await client.get("/api/v1/subscriptions?tenantId=1", headers=staff)).status_code == 200
    put = await client.put("/api/v1/subscriptions/AV-01?tenantId=1", headers=staff,
                           json={"services": [], "notifyRiders": False})
    assert put.status_code == 403


async def test_intelligence_services_list(client, auth):
    response = await client.get("/api/v1/intelligence-services", headers=await auth.headers("rider"))
    assert response.status_code == 200
    available = {s["serviceCode"]: s["available"] for s in response.json()}
    assert available == {"ACCIDENT": True, "EMERGENCY": True, "SECURITY": True, "SAFETY": True,
                         "PASSENGER": False, "VEHICLE_HEALTH": False}


async def test_defaults_for_a_new_vehicle(client, db, service_headers):
    db.add(Vehicle(tenant_id=2, vehicle_code="AV-15", vin="5YJ3E1EA7KF300015", make="Tesla", model="Model 3",
                   model_year=2024, label="Shuttle 6", status="REGISTERED"))
    await db.commit()
    first = await client.post("/api/v1/internal/subscriptions/defaults", headers=service_headers,
                              json={"vehicleId": "AV-15", "tenantId": 2})
    assert first.status_code == 201, first.text
    assert first.json()["created"] == 4
    codes = sorted(s["serviceCode"] for s in first.json()["services"])
    assert codes == ["ACCIDENT", "EMERGENCY", "SAFETY", "SECURITY"]
    assert all(s["channels"] == ["DASHBOARD", "EMAIL"] and s["enabled"] for s in first.json()["services"])
    second = await client.post("/api/v1/internal/subscriptions/defaults", headers=service_headers,
                               json={"vehicleId": "AV-15", "tenantId": 2})
    assert second.status_code == 201 and second.json()["created"] == 0
    unknown = await client.post("/api/v1/internal/subscriptions/defaults", headers=service_headers,
                                json={"vehicleId": "AV-99", "tenantId": 2})
    assert unknown.status_code == 404
    # Internal routes refuse user tokens.
    assert (await client.get("/api/v1/internal/subscriptions?tenantId=2")).status_code == 401
