"""Internal routes, for other components only (Part III, Section 2.3, Table 85).

    POST /internal/events/{eventRef}/result   the classifier worker's result -> {decision}

Service token only (Table 89): no token or a bad one is 401, a valid user token 403.
nginx does not route /internal/ from the Internet, so only containers on the Docker
network can reach it.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..auth import TenantDirectory
from ..deps import get_mongo, get_notifier, get_publisher, get_session_factory, get_tenants, service_caller
from ..schemas import ResultBody, ResultResponse
from ..services.alert_engine import AlertEngine
from ..services.event_store import ResultEvents

router = APIRouter(prefix="/internal", tags=["internal"])


def get_engine(session_factory: Any = Depends(get_session_factory), mongo: Any = Depends(get_mongo),
               publisher: Any = Depends(get_publisher), tenants: TenantDirectory = Depends(get_tenants),
               notifier: Any = Depends(get_notifier)) -> AlertEngine:
    return AlertEngine(session_factory, ResultEvents(mongo), publisher, tenants, notifier)


@router.post("/events/{eventRef}/result", response_model=ResultResponse, response_model_exclude_none=True)
async def post_result(eventRef: str, body: ResultBody, _service: dict = Depends(service_caller),
                      engine: AlertEngine = Depends(get_engine)):
    """Decides on the stored audio event `eventRef` and creates at most one alert. 404 when the event does not
    exist for that tenant and vehicle; 400 when topK is empty without an error. Idempotent (BR-C3-12)."""
    decision = await engine.handle(eventRef, body.tenantId, body.vehicleId,
                                   [(s.class_, s.conf) for s in body.topK], body.modelVersion, body.error)
    return {"decision": decision}
