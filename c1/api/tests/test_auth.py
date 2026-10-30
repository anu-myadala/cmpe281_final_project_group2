"""BR-02 login decision table (Part I, Table 25): one test per column R1..R6,
plus refresh, logout, /auth/me and the password change."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.models import AppUser, AuditLog, utcnow

PASSWORD = "Passw0rd!demo"


async def _make_user(client, auth, db, container, email, role="OWNER", tenant_id=1, name="Test User"):
    """Create a user through the admin API and give it the demo password."""
    response = await client.post("/api/v1/admin/users", headers=await auth.headers("admin"),
                                 json={"email": email, "name": name, "role": role, "tenantId": tenant_id})
    assert response.status_code == 201, response.text
    user = await db.get(AppUser, response.json()["userId"])
    user.password_hash = container.hasher.hash(PASSWORD)
    user.must_change_password = False
    await db.commit()
    return user


async def _audit_actions(db, user_id):
    rows = await db.execute(select(AuditLog.action).where(AuditLog.entity == "app_user",
                                                          AuditLog.entity_id == str(user_id)))
    return [r[0] for r in rows]


async def test_r1_unknown_email_is_generic_401(auth):
    response = await auth.login("nobody@example.com")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_r1_disabled_user_is_generic_401(client, auth, db, container):
    user = await _make_user(client, auth, db, container, "disabled@test.example")
    user.status = "DISABLED"
    await db.commit()
    response = await auth.login("disabled@test.example")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_r2_suspended_tenant_is_403(client, auth, db, container):
    admin = await auth.headers("admin")
    tenant = (await client.post("/api/v1/admin/tenants", headers=admin,
                                json={"name": "Suspended Co", "tenantType": "FLEET", "plan": "FLEET"})).json()
    await _make_user(client, auth, db, container, "fm@suspended.example", "FLEET_MANAGER", tenant["tenantId"])
    patched = await client.patch(f"/api/v1/admin/tenants/{tenant['tenantId']}", headers=admin, json={"status": "SUSPENDED"})
    assert patched.status_code == 200 and patched.json()["status"] == "SUSPENDED"
    response = await auth.login("fm@suspended.example")
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "TENANT_SUSPENDED"
    # Reactivation lets the user in again (BR-06 keeps the data).
    await client.patch(f"/api/v1/admin/tenants/{tenant['tenantId']}", headers=admin, json={"status": "ACTIVE"})
    assert (await auth.login("fm@suspended.example")).status_code == 200


async def test_r3_locked_account_is_423_even_with_right_password(client, auth, db, container):
    user = await _make_user(client, auth, db, container, "locked@test.example")
    user.status = "LOCKED"
    user.locked_until = utcnow() + timedelta(minutes=10)
    await db.commit()
    response = await auth.login("locked@test.example")
    assert response.status_code == 423
    body = response.json()["error"]
    assert body["code"] == "LOCKED" and body["details"]["lockedUntil"].endswith("Z")


async def test_r4_wrong_password_counts_a_failure(client, auth, db, container):
    user = await _make_user(client, auth, db, container, "wrongpw@test.example")
    response = await auth.login("wrongpw@test.example", "not-the-password")
    assert response.status_code == 401
    await db.refresh(user)
    assert user.failed_logins == 1 and user.last_failed_at is not None and user.status == "ACTIVE"
    assert "LOGIN_FAIL" in await _audit_actions(db, user.user_id)


async def test_r5_fifth_failure_locks_for_15_minutes(client, auth, db, container):
    user = await _make_user(client, auth, db, container, "fifth@test.example")
    for _ in range(4):
        assert (await auth.login("fifth@test.example", "bad")).status_code == 401
    response = await auth.login("fifth@test.example", "bad")
    assert response.status_code == 423
    await db.refresh(user)
    assert user.status == "LOCKED" and user.failed_logins == 5
    assert timedelta(minutes=14) < user.locked_until - utcnow() <= timedelta(minutes=15)
    assert "LOGIN_LOCK" in await _audit_actions(db, user.user_id)
    # The right password is refused while the lock lasts.
    assert (await auth.login("fifth@test.example")).status_code == 423


async def test_r6_success_resets_counters_and_issues_tokens(client, auth, db, container):
    user = await _make_user(client, auth, db, container, "ok@test.example")
    user.failed_logins = 3
    user.last_failed_at = utcnow()
    await db.commit()
    response = await auth.login("ok@test.example")
    assert response.status_code == 200
    body = response.json()
    assert body["accessToken"] and body["mustChangePassword"] is False
    assert body["user"]["email"] == "ok@test.example" and body["user"]["tenantName"] == "Nguyen household"
    cookie = response.headers.get("set-cookie", "")
    assert "refreshToken=" in cookie and "HttpOnly" in cookie
    await db.refresh(user)
    assert user.failed_logins == 0 and user.last_login_at is not None
    assert "LOGIN" in await _audit_actions(db, user.user_id)
    claims = container.tokens.verify(body["accessToken"])
    assert claims.role == "OWNER" and claims.tenant_id == 1 and claims.plan == "BASIC" and claims.jti


async def test_window_reset_after_15_minutes(client, auth, db, container):
    user = await _make_user(client, auth, db, container, "window@test.example")
    user.failed_logins = 4
    user.last_failed_at = utcnow() - timedelta(minutes=16)
    await db.commit()
    assert (await auth.login("window@test.example", "bad")).status_code == 401  # counter restarted at 1
    await db.refresh(user)
    assert user.failed_logins == 1 and user.status == "ACTIVE"


async def test_refresh_me_logout(client, auth):
    login = await auth.login("anh")
    assert login.status_code == 200
    token = login.json()["accessToken"]
    refreshed = await client.post("/api/v1/auth/refresh", cookies=login.cookies)
    assert refreshed.status_code == 200 and refreshed.json()["accessToken"]
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["user"]["role"] == "OWNER"
    assert "subscriptions" in me.json()["permissions"] and "users" not in me.json()["permissions"]
    out = await client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}, cookies=login.cookies)
    assert out.status_code == 204
    # The revoked access token and refresh token are refused afterwards.
    assert (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})).status_code == 401
    assert (await client.post("/api/v1/auth/refresh", cookies=login.cookies)).status_code == 401
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_password_change_rules(client, auth, db, container):
    user = await _make_user(client, auth, db, container, "pw@test.example")
    user.must_change_password = True
    await db.commit()
    login = await auth.login("pw@test.example")
    assert login.json()["mustChangePassword"] is True
    headers = {"Authorization": f"Bearer {login.json()['accessToken']}"}
    weak = await client.post("/api/v1/auth/password/change", headers=headers,
                             json={"currentPassword": PASSWORD, "newPassword": "short"})
    assert weak.status_code == 400 and weak.json()["error"]["code"] == "VALIDATION"
    same = await client.post("/api/v1/auth/password/change", headers=headers,
                             json={"currentPassword": PASSWORD, "newPassword": "pw@test.example"})
    assert same.status_code == 400
    wrong = await client.post("/api/v1/auth/password/change", headers=headers,
                              json={"currentPassword": "nope", "newPassword": "Another-Secret-12"})
    assert wrong.status_code == 401
    ok = await client.post("/api/v1/auth/password/change", headers=headers,
                           json={"currentPassword": PASSWORD, "newPassword": "Another-Secret-12"})
    assert ok.status_code == 204
    relogin = await auth.login("pw@test.example", "Another-Secret-12")
    assert relogin.status_code == 200 and relogin.json()["mustChangePassword"] is False
    assert "PASSWORD_CHANGE" in await _audit_actions(db, user.user_id)


async def test_missing_token_and_service_token_are_401(client, service_headers):
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    response = await client.get("/api/v1/auth/me", headers=service_headers)
    assert response.status_code == 401
