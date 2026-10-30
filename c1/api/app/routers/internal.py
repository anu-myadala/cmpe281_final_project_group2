"""Internal endpoints for the other components (service token only):
POST /internal/events, POST /internal/subscriptions/defaults, GET /internal/subscriptions."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..container import AppContainer
from ..deps import get_container, get_session, require_service_token
from ..errors import NotFound
from ..models import Vehicle
from ..schemas import InternalEventBody, SubscriptionDefaultsBody
from ..services import SubscriptionService
from ..services.subscription_service import service_entry
from ..repos import IntelligenceServiceRepo

router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(require_service_token)])


@router.post("/events", status_code=202)
async def publish_event(body: InternalEventBody, container: AppContainer = Depends(get_container)):
    payload = dict(body.payload)
    if body.tenantId is not None:
        payload["tenantId"] = body.tenantId
    await container.bus.publish(body.topic, payload)
    return Response(status_code=202)


@router.post("/subscriptions/defaults", status_code=201)
async def subscription_defaults(body: SubscriptionDefaultsBody, session: AsyncSession = Depends(get_session),
                                container: AppContainer = Depends(get_container)):
    vehicle = (await session.execute(select(Vehicle).where(
        Vehicle.tenant_id == body.tenantId, Vehicle.vehicle_code == body.vehicleId))).scalar_one_or_none()
    if vehicle is None:
        raise NotFound("Vehicle not found")
    service = SubscriptionService(container, session)
    created = await service.defaults(vehicle)
    services = {s.service_code: s for s in await IntelligenceServiceRepo.list(session)}
    return {"vehicleId": vehicle.vehicle_code, "tenantId": vehicle.tenant_id, "created": len(created),
            "services": [service_entry(r, services[r.service_code]) for r in created]}


@router.get("/subscriptions")
async def internal_subscriptions(tenantId: int, vehicleId: Optional[str] = None,
                                 session: AsyncSession = Depends(get_session),
                                 container: AppContainer = Depends(get_container)):
    return await SubscriptionService(container, session).internal_rows(tenantId, vehicleId)
