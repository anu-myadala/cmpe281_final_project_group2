"""ReportRouter: GET /reports/{type}?from&to&vehicleId&tenantId&format=json|csv."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..container import AppContainer
from ..core.rbac import RbacGuard, scope_dependency
from ..core.security import Claims
from ..core.timeutil import parse_range
from ..deps import get_container, get_session
from ..services import ReportService

router = APIRouter(prefix="/reports", tags=["reports"])

REPORT_ROLES = ("OWNER", "FLEET_MANAGER", "SERVICE_STAFF", "SYS_ADMIN")


@router.get("/{report_type}")
async def report(report_type: str, request: Request, from_: Optional[str] = Query(None, alias="from"),
                 to: Optional[str] = None, vehicleId: Optional[str] = None,
                 format: str = Query("json", pattern="^(json|csv)$"),
                 claims: Claims = Depends(RbacGuard.requires(*REPORT_ROLES)),
                 session: AsyncSession = Depends(get_session), container: AppContainer = Depends(get_container)):
    scope = await scope_dependency(request, claims, session)
    start, end = parse_range(from_, to)
    service = ReportService(container, session)
    if format == "csv":
        return await service.export_csv(report_type, scope, start, end, vehicleId)
    return await service.run(report_type, scope, start, end, vehicleId)
