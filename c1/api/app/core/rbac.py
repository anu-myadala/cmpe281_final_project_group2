"""RbacGuard (BR-01) and TenantContext (BR-03).

RbacGuard.requires(*roles) is a FastAPI dependency declared on the routes;
page_permissions(role) is the access matrix of Table 26 that GET /auth/me
returns so that the menu and the API cannot disagree.

TenantContext.resolve turns the verified claims (and, for staff roles, the
tenantId query parameter) into the Scope every repository query uses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import Forbidden, NotFound, Validation
from ..models import STAFF_ROLES, Tenant, Vehicle, VehicleRider, utcnow
from .security import Claims

# Page keys per role (Part I, Table 26). The front end builds its routes from this list.
PAGE_PERMISSIONS = {
    "OWNER": ["overview", "map", "vehicles", "alerts", "subscriptions", "reports"],
    "FLEET_MANAGER": ["overview", "map", "vehicles", "alerts", "subscriptions", "reports", "stats", "alert-rules"],
    "RIDER": ["alerts"],
    "IOT_ENGINEER": ["vehicles", "devices", "provisioning", "models", "tenants"],
    "SERVICE_STAFF": ["overview", "map", "vehicles", "alerts", "subscriptions", "reports", "stats",
                      "alert-rules", "models", "tenants"],
    "SYS_ADMIN": ["overview", "map", "vehicles", "alerts", "subscriptions", "reports", "stats", "alert-rules",
                  "models", "tenants", "users", "alert-types", "audit", "health"],
}


def _claims_dependency():
    # Imported lazily to avoid a circular import with app.deps.
    from ..deps import current_claims
    return current_claims


class RbacGuard:
    @staticmethod
    def page_permissions(role: str) -> List[str]:
        return list(PAGE_PERMISSIONS.get(role, []))

    @staticmethod
    def requires(*roles: str):
        """Dependency: the caller must have one of the roles, else 403."""
        allowed = set(roles)

        async def _guard(claims: Claims = Depends(_claims_dependency())) -> Claims:
            if claims.role not in allowed:
                raise Forbidden()
            return claims

        return _guard


@dataclass
class Scope:
    """Data scope of one request (BR-03)."""
    tenant_id: int
    tenant_code: str
    tenant_name: str
    plan: str
    time_zone: str
    role: str
    user_id: int
    vehicle_ids: Optional[List[int]] = field(default=None)  # RIDER: vehicles with an active rider row

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF_ROLES


class TenantContext:
    """resolve(claims, requested_tenant_id?) -> Scope.

    Tenant roles always get their own tenant (a tenantId parameter is ignored);
    staff roles must pass tenantId (400 without it, 404 for an unknown one);
    a RIDER is limited to the vehicles with an active vehicle_rider row.
    """

    @staticmethod
    async def resolve(session: AsyncSession, claims: Claims, requested_tenant_id: Optional[int] = None) -> Scope:
        if claims.role in STAFF_ROLES:
            if requested_tenant_id is None:
                raise Validation("tenantId is required for staff roles", {"field": "tenantId"})
            tenant_id = requested_tenant_id
        else:
            if claims.tenant_id is None:
                raise Forbidden("Tenant role without a tenant")
            tenant_id = claims.tenant_id
        tenant = await session.get(Tenant, tenant_id)
        if tenant is None:
            raise NotFound("Tenant not found")
        scope = Scope(tenant_id=tenant.tenant_id, tenant_code=tenant.code, tenant_name=tenant.name,
                      plan=tenant.plan, time_zone=tenant.time_zone, role=claims.role, user_id=claims.user_id)
        if claims.role == "RIDER":
            now = utcnow()
            rows = await session.execute(
                select(VehicleRider.vehicle_id).where(
                    VehicleRider.user_id == claims.user_id, VehicleRider.tenant_id == tenant.tenant_id,
                    VehicleRider.valid_from <= now,
                    (VehicleRider.valid_to.is_(None)) | (VehicleRider.valid_to > now)))
            scope.vehicle_ids = [r[0] for r in rows]
        return scope


async def scope_dependency(request: Request, claims: Claims, session: AsyncSession) -> Scope:
    """Shared helper for the routers: tenantId query parameter -> Scope."""
    raw = request.query_params.get("tenantId")
    requested: Optional[int] = None
    if raw not in (None, ""):
        try:
            requested = int(raw)
        except ValueError:
            raise Validation("tenantId must be an integer", {"field": "tenantId"})
    return await TenantContext.resolve(session, claims, requested)


async def vehicles_of_scope(session: AsyncSession, scope: Scope, include_retired: bool = False) -> List[Vehicle]:
    stmt = select(Vehicle).where(Vehicle.tenant_id == scope.tenant_id).order_by(Vehicle.vehicle_code)
    if not include_retired:
        stmt = stmt.where(Vehicle.status != "RETIRED")
    if scope.vehicle_ids is not None:
        stmt = stmt.where(Vehicle.vehicle_id.in_(scope.vehicle_ids or [-1]))
    return list((await session.execute(stmt)).scalars())


async def vehicle_in_scope(session: AsyncSession, scope: Scope, vehicle_code: str) -> Vehicle:
    """The vehicle with that public code inside the scope, else 404 (never reveals other tenants)."""
    for v in await vehicles_of_scope(session, scope, include_retired=True):
        if v.vehicle_code == vehicle_code:
            return v
    raise NotFound("Vehicle not found")
