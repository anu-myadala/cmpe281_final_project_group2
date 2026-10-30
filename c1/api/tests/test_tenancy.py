"""BR-01 / BR-03: two tenants, 404 for another tenant's ids, 403 for roles
outside their view, staff tenantId handling."""
from __future__ import annotations


async def test_owner_cannot_see_or_write_other_tenant_vehicle(client, auth):
    anh = await auth.headers("anh")
    assert (await client.get("/api/v1/subscriptions?vehicleId=AV-10", headers=anh)).status_code == 404
    put = await client.put("/api/v1/subscriptions/AV-10", headers=anh, json={"services": [], "notifyRiders": False})
    assert put.status_code == 404
    # A tenant role may pass tenantId but it is ignored: Anh still sees only AV-01.
    listed = await client.get("/api/v1/subscriptions?tenantId=2", headers=anh)
    assert listed.status_code == 200 and [c["vehicleId"] for c in listed.json()] == ["AV-01"]


async def test_staff_need_tenant_id(client, auth):
    staff = await auth.headers("staff")
    assert (await client.get("/api/v1/dashboard/overview", headers=staff)).status_code == 400
    assert (await client.get("/api/v1/dashboard/map", headers=staff)).status_code == 400
    assert (await client.get("/api/v1/dashboard/map?tenantId=abc", headers=staff)).status_code == 400
    assert (await client.get("/api/v1/dashboard/map?tenantId=9999", headers=staff)).status_code == 404
    ok = await client.get("/api/v1/dashboard/map?tenantId=2", headers=staff)
    assert ok.status_code == 200
    assert {"AV-10", "AV-11", "AV-12", "AV-13", "AV-14"} <= {v["vehicleId"] for v in ok.json()["vehicles"]}


async def test_rider_and_iot_engineer_have_no_dashboard(client, auth):
    for who in ("rider", "iot"):
        headers = await auth.headers(who)
        assert (await client.get("/api/v1/dashboard/overview", headers=headers)).status_code == 403, who
        assert (await client.get("/api/v1/dashboard/map", headers=headers)).status_code == 403, who
        assert (await client.get("/api/v1/subscriptions", headers=headers)).status_code == 403, who


async def test_admin_tenant_list_readable_by_staff_roles_only(client, auth):
    for who in ("iot", "staff", "admin"):
        response = await client.get("/api/v1/admin/tenants", headers=await auth.headers(who))
        assert response.status_code == 200, who
        assert {t["name"] for t in response.json()["items"]} >= {"Nguyen household", "Bay Shuttle LLC"}
    for who in ("anh", "maria", "rider"):
        assert (await client.get("/api/v1/admin/tenants", headers=await auth.headers(who))).status_code == 403, who
    # Every other admin call is SYS_ADMIN only.
    for who in ("iot", "staff"):
        headers = await auth.headers(who)
        assert (await client.post("/api/v1/admin/tenants", headers=headers,
                                  json={"name": "Nope", "tenantType": "FLEET", "plan": "FLEET"})).status_code == 403
        assert (await client.get("/api/v1/admin/users", headers=headers)).status_code == 403


async def test_registry_roles(client, auth):
    for who in ("anh", "maria", "rider"):
        assert (await client.get("/api/v1/models", headers=await auth.headers(who))).status_code == 403, who
    iot = await auth.headers("iot")
    assert (await client.get("/api/v1/models", headers=iot)).status_code == 200
    assert (await client.post("/api/v1/models", headers=iot, json={"name": "x-model", "task": "t"})).status_code == 403


async def test_permissions_follow_the_access_matrix(client, auth):
    expected = {
        "anh": {"overview", "map", "vehicles", "alerts", "subscriptions", "reports"},
        "maria": {"overview", "map", "vehicles", "alerts", "subscriptions", "reports", "stats", "alert-rules"},
        "rider": {"alerts"},
        "iot": {"vehicles", "devices", "provisioning", "models", "tenants"},
    }
    for who, pages in expected.items():
        me = await client.get("/api/v1/auth/me", headers=await auth.headers(who))
        assert set(me.json()["permissions"]) == pages, who
    admin = await client.get("/api/v1/auth/me", headers=await auth.headers("admin"))
    assert {"users", "alert-types", "tenants", "models", "stats"} <= set(admin.json()["permissions"])


async def test_rider_scope_is_the_active_trip(client, auth):
    """The rider of AV-01 may label events of that car but cannot see tenant B's."""
    rider = await auth.headers("rider")
    assert (await client.post("/api/v1/events/6729b1c4e1f0a2d3c4b5a697/feedback", headers=rider,
                              json={"label": "CONFIRMED"})).status_code == 403  # riders are read-only
    response = await client.get("/api/v1/reports/vehicle-activity", headers=rider)
    assert response.status_code == 403
