"""DashboardRouter: /dashboard/overview, /dashboard/map, /dashboard/stats."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..container import AppContainer
from ..core.rbac import RbacGuard, scope_dependency
from ..core.security import Claims
from ..core.timeutil import parse_range
from ..deps import get_container, get_session
from ..services import DashboardService

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

OVERVIEW_ROLES = ("OWNER", "FLEET_MANAGER", "SERVICE_STAFF", "SYS_ADMIN")
STATS_ROLES = ("FLEET_MANAGER", "SERVICE_STAFF", "SYS_ADMIN")


@router.get("/overview")
async def overview(request: Request, claims: Claims = Depends(RbacGuard.requires(*OVERVIEW_ROLES)),
                   session: AsyncSession = Depends(get_session), container: AppContainer = Depends(get_container)):
    scope = await scope_dependency(request, claims, session)
    return await DashboardService(container, session).overview(scope)


@router.get("/map")
async def vehicle_map(request: Request, claims: Claims = Depends(RbacGuard.requires(*OVERVIEW_ROLES)),
                      session: AsyncSession = Depends(get_session), container: AppContainer = Depends(get_container)):
    scope = await scope_dependency(request, claims, session)
    return await DashboardService(container, session).map(scope)


@router.get("/stats")
async def stats(request: Request, from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
                granularity: str = Query("auto", pattern="^(auto|hour|day)$"),
                claims: Claims = Depends(RbacGuard.requires(*STATS_ROLES)),
                session: AsyncSession = Depends(get_session), container: AppContainer = Depends(get_container)):
    scope = await scope_dependency(request, claims, session)
    start, end = parse_range(from_, to)
    return await DashboardService(container, session).stats(scope, start, end, granularity)
