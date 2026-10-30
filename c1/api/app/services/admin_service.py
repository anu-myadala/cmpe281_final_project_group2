"""AdminService: tenants (BR-06), users and roles (BR-07, decision table of
Part I Table 29) and the platform-wide alert types. Every write is audited;
the invitation and reset emails go out after the commit."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..container import AppContainer
from ..core.audit import AuditLogger, snapshot
from ..core.pagination import Page, page_body
from ..core.timeutil import iso
from ..errors import BusinessRule, Conflict, NotFound, Validation
from ..models import PLAN_MAX_VEHICLES, STAFF_ROLES, AlertType, AppUser, Tenant
from ..repos import AlertTypeRepo, TenantRepo, UserRepo
from ..schemas import AlertTypeBody, TenantCreateBody, TenantPatchBody, UserCreateBody, UserPatchBody


def tenant_dto(t: Tenant, vehicles: int = 0, users: int = 0) -> Dict[str, Any]:
    return {"tenantId": t.tenant_id, "code": t.code, "name": t.name, "tenantType": t.tenant_type, "plan": t.plan,
            "maxVehicles": t.max_vehicles, "timeZone": t.time_zone, "status": t.status, "vehicles": vehicles,
            "users": users, "createdAt": iso(t.created_at), "updatedAt": iso(t.updated_at)}


def admin_user_dto(u: AppUser) -> Dict[str, Any]:
    return {"userId": u.user_id, "email": u.email, "name": u.name, "role": u.role, "tenantId": u.tenant_id,
            "status": u.status, "mustChangePassword": bool(u.must_change_password), "lastLoginAt": iso(u.last_login_at),
            "createdAt": iso(u.created_at)}


def alert_type_dto(a: AlertType) -> Dict[str, Any]:
    return {"code": a.code, "name": a.name, "defaultSeverity": a.default_severity,
            "escalationMinutes": a.escalation_minutes, "description": a.description}


class AdminService:
    def __init__(self, container: AppContainer, session: AsyncSession) -> None:
        self.c = container
        self.session = session

    # -- tenants ----------------------------------------------------------------------
    async def list_tenants(self, page: Page, status: Optional[str], q: Optional[str]) -> Dict[str, Any]:
        rows, total = await TenantRepo.page(self.session, page, status, q)
        vehicles, users = await TenantRepo.counts(self.session, [t.tenant_id for t in rows])
        return page_body([tenant_dto(t, vehicles.get(t.tenant_id, 0), users.get(t.tenant_id, 0)) for t in rows],
                         total, page)

    async def create_tenant(self, body: TenantCreateBody, actor: int, ip: Optional[str]) -> Dict[str, Any]:
        if await TenantRepo.by_name(self.session, body.name):
            raise Conflict("A tenant with that name exists", {"field": "name"})
        tenant = Tenant(code=await TenantRepo.next_code(self.session), name=body.name, tenant_type=body.tenantType,
                        plan=body.plan, max_vehicles=PLAN_MAX_VEHICLES[body.plan],
                        time_zone=body.timeZone or "America/Los_Angeles", status="ACTIVE")
        self.session.add(tenant)
        await self.session.flush()
        AuditLogger.write(self.session, actor, tenant.tenant_id, "TENANT_CREATE", "tenant", tenant.tenant_id, None,
                          snapshot(tenant), ip)
        await self.session.commit()
        return tenant_dto(tenant)

    async def update_tenant(self, tenant_id: int, body: TenantPatchBody, actor: int, ip: Optional[str]) -> Dict[str, Any]:
        tenant = await TenantRepo.get(self.session, tenant_id)
        if tenant is None:
            raise NotFound("Tenant not found")
        before = snapshot(tenant)
        if body.name is not None and body.name.lower() != tenant.name.lower():
            if await TenantRepo.by_name(self.session, body.name):
                raise Conflict("A tenant with that name exists", {"field": "name"})
            tenant.name = body.name
        if body.plan is not None and body.plan != tenant.plan:
            new_max = PLAN_MAX_VEHICLES[body.plan]
            in_use = await TenantRepo.vehicle_count(self.session, tenant_id)
            if in_use > new_max:  # BR-06: the vehicle limit is max_vehicles by plan
                raise BusinessRule("BR-06", f"The {body.plan} plan allows {new_max} vehicles; the tenant has {in_use}",
                                   field="plan", maxVehicles=new_max, vehicles=in_use)
            tenant.plan = body.plan
            tenant.max_vehicles = new_max
        if body.status is not None:
            tenant.status = body.status  # ACTIVE or SUSPENDED only (schema); never deleted
        if body.timeZone is not None:
            tenant.time_zone = body.timeZone
        await self.session.flush()
        AuditLogger.write(self.session, actor, tenant.tenant_id, "TENANT_UPDATE", "tenant", tenant.tenant_id, before,
                          snapshot(tenant), ip)
        await self.session.commit()
        vehicles, users = await TenantRepo.counts(self.session, [tenant.tenant_id])
        return tenant_dto(tenant, vehicles.get(tenant.tenant_id, 0), users.get(tenant.tenant_id, 0))

    # -- users --------------------------------------------------------------------------
    async def list_users(self, page: Page, tenant_id: Optional[int], role: Optional[str], status: Optional[str],
                         q: Optional[str]) -> Dict[str, Any]:
        rows, total = await UserRepo.page(self.session, page, tenant_id, role, status, q)
        return page_body([admin_user_dto(u) for u in rows], total, page)

    @staticmethod
    def _check_role_tenant(role: str, tenant_id: Optional[int]) -> None:
        """BR-07: staff roles have no tenant, tenant roles must have one (400 VALIDATION)."""
        if role in STAFF_ROLES and tenant_id is not None:
            raise Validation("A staff user must not belong to a tenant", {"rule": "BR-07", "field": "tenantId"})
        if role not in STAFF_ROLES and tenant_id is None:
            raise Validation("A tenant user must belong to a tenant", {"rule": "BR-07", "field": "tenantId"})

    async def create_user(self, body: UserCreateBody, actor: int, ip: Optional[str]) -> Dict[str, Any]:
        email = body.email.strip().lower()
        if await UserRepo.find_by_email(self.session, email):  # R1
            raise Conflict("A user with that email exists", {"field": "email"})
        self._check_role_tenant(body.role, body.tenantId)  # R2, R4
        if body.tenantId is not None and await TenantRepo.get(self.session, body.tenantId) is None:
            raise NotFound("Tenant not found")
        temp_password = self.c.hasher.random_password()
        user = AppUser(tenant_id=body.tenantId, email=email, name=body.name, role=body.role, status="ACTIVE",
                       password_hash=self.c.hasher.hash(temp_password), must_change_password=True)
        self.session.add(user)
        await self.session.flush()
        AuditLogger.write(self.session, actor, body.tenantId, "USER_CREATE", "app_user", user.user_id, None,
                          snapshot(user), ip)
        await self.session.commit()
        await self.c.mail.send_invitation(user.email, user.name, temp_password)  # after the commit
        return admin_user_dto(user)

    async def update_user(self, user_id: int, body: UserPatchBody, actor: int, ip: Optional[str]) -> Dict[str, Any]:
        user = await UserRepo.get(self.session, user_id)
        if user is None:
            raise NotFound("User not found")
        before = snapshot(user)
        role = body.role or user.role
        tenant_id = body.tenantId if "tenantId" in body.model_fields_set else user.tenant_id
        if body.role is not None and body.role in STAFF_ROLES and "tenantId" not in body.model_fields_set:
            tenant_id = None  # moving to a staff role clears the tenant
        self._check_role_tenant(role, tenant_id)
        if tenant_id is not None and tenant_id != user.tenant_id and await TenantRepo.get(self.session, tenant_id) is None:
            raise NotFound("Tenant not found")
        disabling_admin = (body.status == "DISABLED" and user.role == "SYS_ADMIN" and user.status == "ACTIVE")
        demoting_admin = (body.role is not None and body.role != "SYS_ADMIN" and user.role == "SYS_ADMIN"
                          and user.status == "ACTIVE")
        if (disabling_admin or demoting_admin) and await UserRepo.count_active_admins(self.session) <= 1:  # R6
            raise BusinessRule("BR-07", "The last active administrator cannot be disabled", field="status")
        if body.name is not None:
            user.name = body.name
        user.role = role
        user.tenant_id = tenant_id
        if body.status is not None:
            user.status = body.status
            if body.status == "ACTIVE":
                user.failed_logins = 0
                user.locked_until = None
        await self.session.flush()
        AuditLogger.write(self.session, actor, user.tenant_id, "USER_UPDATE", "app_user", user.user_id, before,
                          snapshot(user), ip)
        await self.session.commit()
        return admin_user_dto(user)

    async def reset_password(self, user_id: int, actor: int, ip: Optional[str]) -> None:
        user = await UserRepo.get(self.session, user_id)
        if user is None:
            raise NotFound("User not found")
        temp_password = self.c.hasher.random_password()
        user.password_hash = self.c.hasher.hash(temp_password)
        user.must_change_password = True
        user.failed_logins = 0
        user.locked_until = None
        if user.status == "LOCKED":
            user.status = "ACTIVE"
        AuditLogger.write(self.session, actor, user.tenant_id, "USER_RESET", "app_user", user.user_id, None,
                          {"mustChangePassword": True}, ip)
        await self.session.commit()
        await self.c.mail.send_reset(user.email, user.name, temp_password)

    # -- alert types ---------------------------------------------------------------------
    async def list_alert_types(self) -> List[Dict[str, Any]]:
        return [alert_type_dto(a) for a in await AlertTypeRepo.list(self.session)]

    async def update_alert_type(self, code: str, body: AlertTypeBody, actor: int, ip: Optional[str]) -> Dict[str, Any]:
        row = await AlertTypeRepo.get(self.session, code)
        if row is None:
            raise NotFound("Alert type not found")  # the six codes are fixed
        before = snapshot(row)
        if body.name is not None:
            row.name = body.name
        if body.defaultSeverity is not None:
            row.default_severity = body.defaultSeverity
        if body.escalationMinutes is not None:
            row.escalation_minutes = body.escalationMinutes
        if body.description is not None:
            row.description = body.description
        AuditLogger.write(self.session, actor, None, "ALERT_TYPE_UPDATE", "alert_type", code, before, snapshot(row), ip)
        await self.session.commit()
        return alert_type_dto(row)
