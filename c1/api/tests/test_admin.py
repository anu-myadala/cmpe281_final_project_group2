"""BR-06 tenants and BR-07 users (Part I, Table 29): one test per column
R1..R6, plus alert types and the pagination limit."""
from __future__ import annotations

from sqlalchemy import select

from app.models import AuditLog


async def test_tenant_create_sets_max_vehicles_from_plan(client, auth, db):
    admin = await auth.headers("admin")
    response = await client.post("/api/v1/admin/tenants", headers=admin,
                                 json={"name": "Campus Rides", "tenantType": "FLEET", "plan": "FLEET"})
    assert response.status_code == 201, response.text
    tenant = response.json()
    assert tenant["maxVehicles"] == 50 and tenant["status"] == "ACTIVE" and tenant["code"].startswith("t")
    assert tenant["timeZone"] == "America/Los_Angeles"
    duplicate = await client.post("/api/v1/admin/tenants", headers=admin,
                                  json={"name": "campus rides", "tenantType": "FLEET", "plan": "BASIC"})
    assert duplicate.status_code == 409
    enterprise = await client.post("/api/v1/admin/tenants", headers=admin,
                                   json={"name": "Big Fleet", "tenantType": "SERVICE_PROVIDER", "plan": "ENTERPRISE",
                                         "timeZone": "America/New_York"})
    assert enterprise.json()["maxVehicles"] == 500 and enterprise.json()["timeZone"] == "America/New_York"
    audits = (await db.execute(select(AuditLog).where(AuditLog.action == "TENANT_CREATE"))).scalars().all()
    assert audits and audits[-1].after_json["name"] == "Big Fleet"


async def test_tenant_patch_plan_below_vehicle_count_is_br06(client, auth):
    admin = await auth.headers("admin")
    # Tenant 2 has five vehicles: the BASIC plan (3) is refused.
    response = await client.patch("/api/v1/admin/tenants/2", headers=admin, json={"plan": "BASIC"})
    assert response.status_code == 422
    details = response.json()["error"]["details"]
    assert details["rule"] == "BR-06" and details["maxVehicles"] == 3 and details["vehicles"] >= 5
    upgrade = await client.patch("/api/v1/admin/tenants/2", headers=admin, json={"plan": "ENTERPRISE"})
    assert upgrade.status_code == 200 and upgrade.json()["maxVehicles"] == 500
    back = await client.patch("/api/v1/admin/tenants/2", headers=admin, json={"plan": "FLEET"})
    assert back.json()["maxVehicles"] == 50


async def test_tenant_patch_rejects_other_statuses_and_unknown_tenant(client, auth):
    admin = await auth.headers("admin")
    bad = await client.patch("/api/v1/admin/tenants/1", headers=admin, json={"status": "DELETED"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "VALIDATION"
    missing = await client.patch("/api/v1/admin/tenants/9999", headers=admin, json={"name": "Nobody Inc"})
    assert missing.status_code == 404
    assert (await client.delete("/api/v1/admin/tenants/1", headers=admin)).status_code == 405


async def test_tenant_list_with_counts_and_filters(client, auth):
    response = await client.get("/api/v1/admin/tenants?q=shuttle", headers=await auth.headers("admin"))
    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 1 and body["size"] == 25 and body["total"] >= 1
    bay = next(t for t in body["items"] if t["name"] == "Bay Shuttle LLC")
    assert bay["vehicles"] >= 5 and bay["users"] >= 1


async def test_user_r1_duplicate_email_is_409(client, auth):
    response = await client.post("/api/v1/admin/users", headers=await auth.headers("admin"),
                                 json={"email": "ANH@example.com", "name": "Dup", "role": "OWNER", "tenantId": 1})
    assert response.status_code == 409


async def test_user_r2_staff_role_with_tenant_is_400(client, auth):
    response = await client.post("/api/v1/admin/users", headers=await auth.headers("admin"),
                                 json={"email": "staff2@scicp.example", "name": "S", "role": "SERVICE_STAFF", "tenantId": 1})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION" and error["details"]["rule"] == "BR-07"


async def test_user_r3_staff_role_without_tenant_is_created_and_invited(client, auth, container):
    sent_before = len(container.mail.sent)
    response = await client.post("/api/v1/admin/users", headers=await auth.headers("admin"),
                                 json={"email": "iot2@scicp.example", "name": "Second Engineer", "role": "IOT_ENGINEER"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ACTIVE" and body["mustChangePassword"] is True and body["tenantId"] is None
    assert len(container.mail.sent) == sent_before + 1
    mail = container.mail.sent[-1]
    assert mail["to"] == "iot2@scicp.example" and "temporary password" in mail["body"]
    # The temporary password from the invitation works and forces a change.
    temp = mail["body"].split("\n\n")[2].strip()
    login = await auth.login("iot2@scicp.example", temp)
    assert login.status_code == 200 and login.json()["mustChangePassword"] is True


async def test_user_r4_tenant_role_without_tenant_is_400(client, auth):
    response = await client.post("/api/v1/admin/users", headers=await auth.headers("admin"),
                                 json={"email": "owner2@example.com", "name": "O", "role": "OWNER"})
    assert response.status_code == 400 and response.json()["error"]["details"]["rule"] == "BR-07"


async def test_user_r5_tenant_role_with_tenant_is_created(client, auth):
    response = await client.post("/api/v1/admin/users", headers=await auth.headers("admin"),
                                 json={"email": "maria2@bayshuttle.example", "name": "Maria Two",
                                       "role": "FLEET_MANAGER", "tenantId": 2})
    assert response.status_code == 201
    assert response.json()["tenantId"] == 2
    unknown_tenant = await client.post("/api/v1/admin/users", headers=await auth.headers("admin"),
                                       json={"email": "x@y.example", "name": "X", "role": "OWNER", "tenantId": 9999})
    assert unknown_tenant.status_code == 404


async def test_user_r6_last_active_admin_cannot_be_disabled(client, auth):
    admin = await auth.headers("admin")
    users = (await client.get("/api/v1/admin/users?role=SYS_ADMIN&status=ACTIVE", headers=admin)).json()
    assert users["total"] == 1
    admin_id = users["items"][0]["userId"]
    refused = await client.patch(f"/api/v1/admin/users/{admin_id}", headers=admin, json={"status": "DISABLED"})
    assert refused.status_code == 422 and refused.json()["error"]["details"]["rule"] == "BR-07"
    demote = await client.patch(f"/api/v1/admin/users/{admin_id}", headers=admin, json={"role": "SERVICE_STAFF"})
    assert demote.status_code == 422
    # With a second administrator the first one can be disabled.
    second = (await client.post("/api/v1/admin/users", headers=admin,
                                json={"email": "admin2@scicp.example", "name": "Second Admin", "role": "SYS_ADMIN"})).json()
    disabled = await client.patch(f"/api/v1/admin/users/{second['userId']}", headers=admin, json={"status": "DISABLED"})
    assert disabled.status_code == 200 and disabled.json()["status"] == "DISABLED"


async def test_user_patch_role_tenant_consistency(client, auth):
    admin = await auth.headers("admin")
    user = (await client.post("/api/v1/admin/users", headers=admin,
                              json={"email": "move@example.com", "name": "Mover", "role": "OWNER", "tenantId": 1})).json()
    bad = await client.patch(f"/api/v1/admin/users/{user['userId']}", headers=admin, json={"tenantId": None})
    assert bad.status_code == 400 and bad.json()["error"]["details"]["rule"] == "BR-07"
    moved = await client.patch(f"/api/v1/admin/users/{user['userId']}", headers=admin, json={"tenantId": 2, "name": "Moved"})
    assert moved.status_code == 200 and moved.json()["tenantId"] == 2 and moved.json()["name"] == "Moved"
    to_staff = await client.patch(f"/api/v1/admin/users/{user['userId']}", headers=admin, json={"role": "IOT_ENGINEER"})
    assert to_staff.status_code == 200 and to_staff.json()["tenantId"] is None
    assert (await client.patch("/api/v1/admin/users/999999", headers=admin, json={"name": "x"})).status_code == 404


async def test_reset_password_emails_temporary_password(client, auth, container):
    admin = await auth.headers("admin")
    user = (await client.post("/api/v1/admin/users", headers=admin,
                              json={"email": "reset@example.com", "name": "Reset Me", "role": "OWNER", "tenantId": 1})).json()
    response = await client.post(f"/api/v1/admin/users/{user['userId']}/reset-password", headers=admin)
    assert response.status_code == 202
    assert container.mail.sent[-1]["subject"] == "SCICP password reset"
    assert (await client.post("/api/v1/admin/users/999999/reset-password", headers=admin)).status_code == 404


async def test_alert_types(client, auth):
    admin = await auth.headers("admin")
    listed = await client.get("/api/v1/admin/alert-types", headers=admin)
    assert listed.status_code == 200
    types = {t["code"]: t for t in listed.json()}
    assert types["ACCIDENT"]["defaultSeverity"] == "CRITICAL" and types["ACCIDENT"]["escalationMinutes"] == 10
    assert types["VEHICLE_HEALTH"]["escalationMinutes"] == 120
    updated = await client.put("/api/v1/admin/alert-types/SAFETY", headers=admin, json={"escalationMinutes": 45})
    assert updated.status_code == 200 and updated.json()["escalationMinutes"] == 45
    await client.put("/api/v1/admin/alert-types/SAFETY", headers=admin, json={"escalationMinutes": 30})
    assert (await client.put("/api/v1/admin/alert-types/NOPE", headers=admin, json={"name": "x"})).status_code == 404
    assert (await client.get("/api/v1/admin/alert-types", headers=await auth.headers("staff"))).status_code == 403


async def test_pagination_limits(client, auth):
    admin = await auth.headers("admin")
    over = await client.get("/api/v1/admin/users?size=101", headers=admin)
    assert over.status_code == 400 and over.json()["error"]["code"] == "VALIDATION"
    zero = await client.get("/api/v1/admin/tenants?page=0", headers=admin)
    assert zero.status_code == 400
    ok = await client.get("/api/v1/admin/users?size=100&page=2", headers=admin)
    assert ok.status_code == 200 and ok.json()["page"] == 2 and ok.json()["size"] == 100
