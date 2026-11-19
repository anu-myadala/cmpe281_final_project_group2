"""M8: the audit log viewer (GET /admin/audit, BR-12) and the platform health
page (GET /admin/health). Both are SYS_ADMIN only."""
from __future__ import annotations


async def test_audit_lists_newest_first_with_actor_and_tenant(client, auth):
    admin = await auth.headers("admin")
    created = await client.post("/api/v1/admin/tenants", headers=admin,
                                json={"name": "Audit Viewer Rides", "tenantType": "FLEET", "plan": "BASIC"})
    assert created.status_code == 201, created.text
    response = await client.get("/api/v1/admin/audit", headers=admin, params={"size": 5})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["page"] == 1 and body["size"] == 5 and body["total"] >= 1
    ids = [row["auditId"] for row in body["items"]]
    assert ids == sorted(ids, reverse=True)
    newest = body["items"][0]
    assert newest["action"] == "TENANT_CREATE" and newest["actor"] == "admin@scicp.example"
    assert newest["entity"] == "tenant" and newest["after"]["name"] == "Audit Viewer Rides"
    assert "TENANT_CREATE" in body["facets"]["actions"] and "tenant" in body["facets"]["entities"]


async def test_audit_filters_by_action_actor_entity_and_dates(client, auth):
    admin = await auth.headers("admin")
    await auth.headers("anh")  # a LOGIN row for anh
    logins = (await client.get("/api/v1/admin/audit", headers=admin,
                               params={"action": "LOGIN", "actor": "anh@"})).json()
    assert logins["total"] >= 1
    assert all(r["action"] == "LOGIN" and r["actor"] == "anh@example.com" for r in logins["items"])
    tenants = (await client.get("/api/v1/admin/audit", headers=admin, params={"entity": "tenant"})).json()
    assert tenants["items"] and all(r["entity"] == "tenant" for r in tenants["items"])
    future = (await client.get("/api/v1/admin/audit", headers=admin, params={"from": "2099-01-01"})).json()
    assert future["total"] == 0 and future["items"] == []
    bad = await client.get("/api/v1/admin/audit", headers=admin, params={"from": "2026-11-10", "to": "2026-11-01"})
    assert bad.status_code == 400


async def test_audit_rows_never_carry_password_hashes(client, auth):
    admin = await auth.headers("admin")
    invited = await client.post("/api/v1/admin/users", headers=admin,
                                json={"email": "audit.check@example.com", "name": "Audit Check", "role": "OWNER",
                                      "tenantId": 1})
    assert invited.status_code == 201, invited.text
    rows = (await client.get("/api/v1/admin/audit", headers=admin, params={"action": "USER_CREATE"})).json()["items"]
    assert rows and "password_hash" not in rows[0]["after"]


async def test_audit_and_health_are_admin_only(client, auth):
    for who in ("anh", "maria", "staff"):
        headers = await auth.headers(who)
        assert (await client.get("/api/v1/admin/audit", headers=headers)).status_code == 403
        assert (await client.get("/api/v1/admin/health", headers=headers)).status_code == 403


async def test_health_reports_each_part_and_degrades_when_c2_is_down(client, auth, container):
    admin = await auth.headers("admin")
    body = (await client.get("/api/v1/admin/health", headers=admin)).json()
    parts = {p["name"]: p for p in body["parts"]}
    assert {"api", "mysql", "mongodb", "mqtt", "c2", "c3"} <= set(parts)
    assert parts["mysql"]["state"] == "UP" and parts["mysql"]["latencyMs"] is not None
    assert parts["c2"]["state"] == "UP" and parts["c3"]["state"] == "UP"
    assert parts["mqtt"]["state"] == "DOWN"  # MQTT_URL is not set in the tests
    container.vehicle_client.fail = True
    degraded = (await client.get("/api/v1/admin/health", headers=admin)).json()
    assert degraded["status"] == "DEGRADED" and "c2" in degraded["down"]
    assert {p["name"]: p for p in degraded["parts"]}["c2"]["detail"] == "C2 unreachable"
