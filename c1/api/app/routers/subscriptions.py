"""SubscriptionRouter: /subscriptions, /subscriptions/{vehicleId}, /intelligence-services."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..container import AppContainer
from ..core.rbac import RbacGuard, scope_dependency
from ..core.security import Claims
from ..deps import client_ip, current_claims, get_container, get_session
from ..repos import IntelligenceServiceRepo
from ..schemas import SubscriptionBody
from ..services import SubscriptionService

router = APIRouter(tags=["subscriptions"])

READ_ROLES = ("OWNER", "FLEET_MANAGER", "SERVICE_STAFF", "SYS_ADMIN")
WRITE_ROLES = ("OWNER", "FLEET_MANAGER")


@router.get("/subscriptions")
async def list_subscriptions(request: Request, vehicleId: Optional[str] = None,
                             claims: Claims = Depends(RbacGuard.requires(*READ_ROLES)),
                             session: AsyncSession = Depends(get_session),
                             container: AppContainer = Depends(get_container)):
    scope = await scope_dependency(request, claims, session)
    return await SubscriptionService(container, session).list(scope, vehicleId)


@router.put("/subscriptions/{vehicleId}")
async def save_subscription(vehicleId: str, body: SubscriptionBody, request: Request,
                            claims: Claims = Depends(RbacGuard.requires(*WRITE_ROLES)),
                            session: AsyncSession = Depends(get_session),
                            container: AppContainer = Depends(get_container)):
    scope = await scope_dependency(request, claims, session)
    return await SubscriptionService(container, session).save(scope, vehicleId, body, claims.user_id, client_ip(request))


@router.get("/intelligence-services")
async def intelligence_services(_: Claims = Depends(current_claims), session: AsyncSession = Depends(get_session)):
    return [{"serviceCode": s.service_code, "name": s.name, "available": bool(s.available),
             "description": s.description} for s in await IntelligenceServiceRepo.list(session)]
