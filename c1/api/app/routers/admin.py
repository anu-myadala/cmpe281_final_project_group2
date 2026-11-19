"""AdminRouter: /admin/tenants, /admin/users, /admin/alert-types, /admin/audit, /admin/health.
SYS_ADMIN only, except GET /admin/tenants, which the other staff roles read
for the tenant selector."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..container import AppContainer
from ..core.pagination import Page, page_params
from ..core.rbac import RbacGuard
from ..core.security import Claims
from ..deps import client_ip, get_container, get_session
from ..schemas import AlertTypeBody, TenantCreateBody, TenantPatchBody, UserCreateBody, UserPatchBody
from ..services import AdminService, HealthService

router = APIRouter(prefix="/admin", tags=["admin"])

admin_only = RbacGuard.requires("SYS_ADMIN")
tenant_list_roles = RbacGuard.requires("SYS_ADMIN", "SERVICE_STAFF", "IOT_ENGINEER")


@router.get("/tenants")
async def list_tenants(status: Optional[str] = None, q: Optional[str] = None, page: Page = Depends(page_params),
                       _: Claims = Depends(tenant_list_roles), session: AsyncSession = Depends(get_session),
                       container: AppContainer = Depends(get_container)):
    return await AdminService(container, session).list_tenants(page, status, q)


@router.post("/tenants", status_code=201)
async def create_tenant(body: TenantCreateBody, request: Request, claims: Claims = Depends(admin_only),
                        session: AsyncSession = Depends(get_session), container: AppContainer = Depends(get_container)):
    return await AdminService(container, session).create_tenant(body, claims.user_id, client_ip(request))


@router.patch("/tenants/{tenant_id}")
async def update_tenant(tenant_id: int, body: TenantPatchBody, request: Request, claims: Claims = Depends(admin_only),
                        session: AsyncSession = Depends(get_session), container: AppContainer = Depends(get_container)):
    return await AdminService(container, session).update_tenant(tenant_id, body, claims.user_id, client_ip(request))


@router.get("/users")
async def list_users(tenantId: Optional[int] = None, role: Optional[str] = None, status: Optional[str] = None,
                     q: Optional[str] = None, page: Page = Depends(page_params), _: Claims = Depends(admin_only),
                     session: AsyncSession = Depends(get_session), container: AppContainer = Depends(get_container)):
    return await AdminService(container, session).list_users(page, tenantId, role, status, q)


@router.post("/users", status_code=201)
async def create_user(body: UserCreateBody, request: Request, claims: Claims = Depends(admin_only),
                      session: AsyncSession = Depends(get_session), container: AppContainer = Depends(get_container)):
    return await AdminService(container, session).create_user(body, claims.user_id, client_ip(request))


@router.patch("/users/{user_id}")
async def update_user(user_id: int, body: UserPatchBody, request: Request, claims: Claims = Depends(admin_only),
                      session: AsyncSession = Depends(get_session), container: AppContainer = Depends(get_container)):
    return await AdminService(container, session).update_user(user_id, body, claims.user_id, client_ip(request))


@router.post("/users/{user_id}/reset-password", status_code=202)
async def reset_password(user_id: int, request: Request, claims: Claims = Depends(admin_only),
                         session: AsyncSession = Depends(get_session),
                         container: AppContainer = Depends(get_container)):
    await AdminService(container, session).reset_password(user_id, claims.user_id, client_ip(request))
    return Response(status_code=202)


@router.get("/alert-types")
async def list_alert_types(_: Claims = Depends(admin_only), session: AsyncSession = Depends(get_session),
                           container: AppContainer = Depends(get_container)):
    return await AdminService(container, session).list_alert_types()


@router.put("/alert-types/{code}")
async def update_alert_type(code: str, body: AlertTypeBody, request: Request, claims: Claims = Depends(admin_only),
                            session: AsyncSession = Depends(get_session),
                            container: AppContainer = Depends(get_container)):
    return await AdminService(container, session).update_alert_type(code, body, claims.user_id, client_ip(request))


@router.get("/audit")
async def list_audit(actor: Optional[str] = None, entity: Optional[str] = None, action: Optional[str] = None,
                     tenantId: Optional[int] = None, from_: Optional[str] = Query(None, alias="from"),
                     to: Optional[str] = None, page: Page = Depends(page_params), _: Claims = Depends(admin_only),
                     session: AsyncSession = Depends(get_session), container: AppContainer = Depends(get_container)):
    """M8 audit log viewer (BR-12): newest first, filtered by actor email, entity, action, tenant and dates."""
    return await AdminService(container, session).list_audit(page, actor, entity, action, tenantId, from_, to)


@router.get("/health")
async def platform_health(_: Claims = Depends(admin_only), session: AsyncSession = Depends(get_session),
                          container: AppContainer = Depends(get_container)):
    """M8 platform health: one row per dependency (database, MongoDB, MQTT, C2, C3, classifier workers)."""
    return await HealthService(container, session).report()

