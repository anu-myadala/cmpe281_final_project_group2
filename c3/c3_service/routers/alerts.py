"""The routes of the alert API (Part III, Table 83), mounted under /api/v1.

    GET /alerts               the queue: filters, sort, paging        -> AlertPage
    GET /alerts/map           pins of the open alerts (at most 500)   -> [MapPoint]
    GET /alerts/{alertId}     the alert page in one call              -> AlertDetail
    POST /alerts/{alertId}/actions   Acknowledge, Resolve, False alarm -> AlertRow

Every route depends on alert_caller (deps.py): a valid token, IOT_ENGINEER refused,
the tenant scope resolved. The query parameters are declared in the stub's order and
with its names (from, limit, ...), so C1's AlertClient and the web app call C3
unchanged. /alerts/map is declared before /alerts/{alert_id}, so "map" isn't taken
for an id.
"""
from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Query

from ..auth import Caller, TenantDirectory
from ..deps import alert_caller, get_c1_client, get_mongo, get_publisher, get_session_factory, get_tenants
from ..schemas import ActionBody, AlertDetail, AlertPage, AlertRow, MapPoint
from ..services.alert_actions import AlertActionService
from ..services.alert_query import AlertFilters, AlertQueryService
from ..services.c1_client import C1Client
from ..services.event_store import EventStore

router = APIRouter(tags=["alerts"])


def get_query_service(session_factory: Any = Depends(get_session_factory), mongo: Any = Depends(get_mongo),
                      tenants: TenantDirectory = Depends(get_tenants)) -> AlertQueryService:
    return AlertQueryService(session_factory, EventStore(mongo), tenants)


@router.get("/alerts", response_model=AlertPage)
async def list_alerts(status: Optional[str] = None, type: Optional[str] = None, severity: Optional[str] = None,
                      vehicleId: Optional[str] = None, from_: Optional[str] = Query(default=None, alias="from"),
                      to: Optional[str] = None, sort: str = "createdAt", page: Optional[int] = None,
                      size: Optional[int] = None, limit: Optional[int] = None,
                      caller: Caller = Depends(alert_caller), service: AlertQueryService = Depends(get_query_service)):
    """status NEW, ACKNOWLEDGED, RESOLVED, FALSE_ALARM, OPEN (NEW+ACKNOWLEDGED), CLOSED, or a comma list;
    sort createdAt or severity; page from 1; size 1..100 (default 25), `limit` is an alias of size."""
    filters = AlertFilters(status=status, type=type, severity=severity, vehicle_id=vehicleId, from_=from_, to=to,
                           sort=sort, page=page, size=size or limit)
    return await service.list_alerts(caller, filters)


@router.get("/alerts/map", response_model=List[MapPoint])
async def alerts_map(status: Optional[str] = "OPEN", caller: Caller = Depends(alert_caller),
                     service: AlertQueryService = Depends(get_query_service)):
    """Position: the alert row, else its audio event, else the car's newest telemetry; alerts without one are skipped."""
    return await service.map_points(caller, status)


@router.get("/alerts/{alert_id}", response_model=AlertDetail)
async def get_alert(alert_id: int, caller: Caller = Depends(alert_caller),
                    service: AlertQueryService = Depends(get_query_service)):
    """404 for an unknown alert and for one outside the caller's tenant or trip."""
    return await service.get_detail(caller, alert_id)


def get_action_service(session_factory: Any = Depends(get_session_factory), publisher: Any = Depends(get_publisher),
                       c1_client: C1Client = Depends(get_c1_client)) -> AlertActionService:
    return AlertActionService(session_factory, publisher, c1_client)


@router.post("/alerts/{alert_id}/actions", response_model=AlertRow)
async def post_action(alert_id: int, body: ActionBody, caller: Caller = Depends(alert_caller),
                      service: AlertActionService = Depends(get_action_service)):
    """ACKNOWLEDGE, RESOLVE or FALSE_ALARM; answers the alert in its new state.
    403 RIDER and IOT_ENGINEER; 404 outside the caller's scope; 409 when BR-C3-07 refuses the transition
    or another user changed the alert first. Publishes alert.updated after the commit."""
    return await service.act(caller, alert_id, body.action, body.note)
