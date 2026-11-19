"""BillingRouter (report section 5.1): the price list, a tenant's monthly invoice, all tenants.

    GET /billing/prices                         every role with the billing page
    PUT /billing/prices/{plan}                  SYS_ADMIN, audited PRICE_UPDATE
    GET /billing/invoice?month=YYYY-MM          OWNER, FLEET_MANAGER (own tenant); staff with tenantId
    GET /billing/summary?month=YYYY-MM          SERVICE_STAFF, SYS_ADMIN: one row per tenant
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..container import AppContainer
from ..core.rbac import RbacGuard, scope_dependency
from ..core.security import Claims
from ..deps import client_ip, get_container, get_session
from ..schemas import PriceBody
from ..services import BillingService

router = APIRouter(prefix="/billing", tags=["billing"])

BILLING_ROLES = ("OWNER", "FLEET_MANAGER", "SERVICE_STAFF", "SYS_ADMIN")
SUMMARY_ROLES = ("SERVICE_STAFF", "SYS_ADMIN")


@router.get("/prices")
async def prices(_: Claims = Depends(RbacGuard.requires(*BILLING_ROLES)), session: AsyncSession = Depends(get_session),
                 container: AppContainer = Depends(get_container)):
    return await BillingService(container, session).prices()


@router.put("/prices/{plan}")
async def update_price(plan: str, body: PriceBody, request: Request,
                       claims: Claims = Depends(RbacGuard.requires("SYS_ADMIN")),
                       session: AsyncSession = Depends(get_session), container: AppContainer = Depends(get_container)):
    return await BillingService(container, session).update_price(plan, body, claims.user_id, client_ip(request))


@router.get("/invoice")
async def invoice(request: Request, month: Optional[str] = None,
                  claims: Claims = Depends(RbacGuard.requires(*BILLING_ROLES)),
                  session: AsyncSession = Depends(get_session), container: AppContainer = Depends(get_container)):
    scope = await scope_dependency(request, claims, session)
    return await BillingService(container, session).invoice(scope.tenant_id, month)


@router.get("/summary")
async def summary(month: Optional[str] = None, _: Claims = Depends(RbacGuard.requires(*SUMMARY_ROLES)),
                  session: AsyncSession = Depends(get_session), container: AppContainer = Depends(get_container)):
    return await BillingService(container, session).summary(month)
