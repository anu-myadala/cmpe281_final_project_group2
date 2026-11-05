"""The alert queue, the map pins and the alert detail (modules M3 and M6 of Part III,
Table 79; the API of Section 2.1, Table 83). Read-only: actions are step 5.

The output is the stub's (stubs/c3.py) field for field, with three of the team's decisions:
  * the map takes the alert row's lat/lon first, then the audio event's, then the car's
    newest telemetry (the stub looks at the event first; both are written from the same
    position, so the pins are the same);
  * notifications is [] until the notification table is filled (step 8); the stub
    derives a list from the subscription instead;
  * the audio event is looked up with the alert's tenant code as well as its _id (O6).

Scoping (Table 89): a tenant role sees its own tenant, staff the tenant they chose
(or any tenant for the detail), a RIDER only the cars of the active trip, and an
alert outside the scope is answered as 404, as if it didn't exist.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import case, func, select

from ..auth import Caller, TenantDirectory
from ..errors import NotFound, Validation
from ..models import OPEN_STATUSES, Alert, AlertAction, AlertRule, AppUser, sound_class_name
from ..timeutil import as_naive_utc, iso, parse_iso, utcnow
from .event_store import EventStore, location_of

PAGE_SIZE_DEFAULT = 25
PAGE_SIZE_MAX = 100
MAP_LIMIT = 500  # the map is bounded by design (Section 2.7)
STATUS_FILTERS = {"NEW", "ACKNOWLEDGED", "RESOLVED", "FALSE_ALARM", "OPEN", "CLOSED"}
SEVERITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
CLIP_NOTE = "clip storage is served by the C2 component"


# -- request helpers ------------------------------------------------------------------------------
def parse_statuses(value: Optional[str]) -> Optional[List[str]]:
    """'OPEN' -> NEW, ACKNOWLEDGED; 'CLOSED' -> RESOLVED, FALSE_ALARM; comma lists allowed; None = all."""
    if not value:
        return None
    wanted: List[str] = []
    for part in str(value).split(","):
        part = part.strip().upper()
        if not part:
            continue
        if part not in STATUS_FILTERS:
            raise Validation("Unknown status filter", {"field": "status", "allowed": sorted(STATUS_FILTERS)})
        if part == "OPEN":
            wanted.extend(OPEN_STATUSES)
        elif part == "CLOSED":
            wanted.extend(("RESOLVED", "FALSE_ALARM"))
        else:
            wanted.append(part)
    return wanted or None


def page_params(page: Optional[int], size: Optional[int]) -> Tuple[int, int]:
    """1-based page, size 1..100 (default 25); missing or 0 means the default, as in the stub."""
    page = page or 1
    size = size or PAGE_SIZE_DEFAULT
    if page < 1:
        raise Validation("page must be 1 or more", {"field": "page"})
    if size < 1 or size > PAGE_SIZE_MAX:
        raise Validation(f"size must be between 1 and {PAGE_SIZE_MAX}", {"field": "size", "max": PAGE_SIZE_MAX})
    return page, size


def require_tenant(caller: Caller) -> None:
    if caller.tenant_id is None:
        raise Validation("tenantId is required", {"field": "tenantId"})


def scope(stmt, caller: Caller):
    """Restrict a SELECT on alert to what the caller may see."""
    if caller.tenant_id is not None:
        stmt = stmt.where(Alert.tenant_id == caller.tenant_id)
    if caller.rider_vehicle_codes is not None:
        stmt = stmt.where(Alert.vehicle_id.in_(caller.rider_vehicle_codes or ["-"]))
    return stmt


@dataclass
class AlertFilters:
    status: Optional[str] = None
    type: Optional[str] = None
    severity: Optional[str] = None
    vehicle_id: Optional[str] = None
    from_: Optional[str] = None
    to: Optional[str] = None
    sort: str = "createdAt"
    page: Optional[int] = None
    size: Optional[int] = None  # `limit` is folded in by the router


# -- DTOs -------------------------------------------------------------------------------------------
def alert_row(row: Alert, names: Dict[int, str], now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or utcnow()
    created = as_naive_utc(row.created_at)
    return {"alertId": row.alert_id, "vehicleId": row.vehicle_id, "type": row.alert_type_code, "severity": row.severity,
            "status": row.status, "topClass": sound_class_name(row.top_class_id),
            "confidence": float(row.confidence) if row.confidence is not None else None,
            "createdAt": iso(created), "ageSeconds": max(0, int((now - created).total_seconds())) if created else None,
            "acknowledgedBy": names.get(row.acknowledged_by) if row.acknowledged_by else None,
            "acknowledgedAt": iso(row.acknowledged_at), "closedBy": names.get(row.closed_by) if row.closed_by else None,
            "closedAt": iso(row.closed_at), "vehicleState": row.vehicle_state, "count": row.count,
            "lat": row.lat, "lon": row.lon, "eventRef": row.event_ref, "ruleId": row.rule_id,
            "modelVersion": row.model_version, "updatedAt": iso(row.updated_at)}


def event_info(doc: Dict[str, Any]) -> Dict[str, Any]:
    inference = doc.get("inference") or {}
    return {"eventRef": str(doc.get("_id")), "eventId": doc.get("eventId"), "ts": iso(as_naive_utc(doc.get("ts"))),
            "channel": doc.get("channel"), "deviceId": doc.get("deviceId"), "location": doc.get("location"),
            "context": doc.get("context"), "topK": inference.get("topK"), "modelVersion": inference.get("modelVersion"),
            "latencyMs": inference.get("latencyMs"), "edge": doc.get("edge"), "decision": doc.get("decision")}


def rule_info(rule: Optional[AlertRule]) -> Optional[Dict[str, Any]]:
    if rule is None:
        return None
    return {"ruleId": rule.rule_id, "scope": "DEFAULT" if rule.tenant_id is None else "TENANT",
            "alertType": rule.alert_type_code, "class": sound_class_name(rule.class_id),
            "minConfidence": float(rule.min_confidence), "vehicleState": rule.vehicle_state}


def clip_info(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The clip of the event; url stays null until C2's clip service exists (the player is hidden)."""
    clip = (doc or {}).get("clip")
    if not clip:
        return None
    return {"clipId": clip.get("clipId"), "s3Key": clip.get("s3Key"), "url": None, "expiresAt": None,
            "durationMs": clip.get("durationMs"), "sampleRate": clip.get("sampleRate"), "note": CLIP_NOTE}


def action_entry(a: AlertAction, names: Dict[int, str]) -> Dict[str, Any]:
    name = names.get(a.user_id) if a.user_id else None
    at = iso(as_naive_utc(a.ts))
    return {"action": a.action, "userId": a.user_id, "name": name,
            "by": name if a.user_id else ("system" if a.action == "CREATE" else None),
            "fromStatus": a.from_status, "toStatus": a.to_status, "note": a.note, "ts": at, "at": at}


# -- the service ------------------------------------------------------------------------------------
class AlertQueryService:
    def __init__(self, session_factory: Any, events: EventStore, tenants: TenantDirectory) -> None:
        self.session_factory = session_factory
        self.events = events
        self.tenants = tenants

    @staticmethod
    async def _user_names(session, user_ids: Iterable[Optional[int]]) -> Dict[int, str]:
        ids = sorted({int(u) for u in user_ids if u is not None})
        if not ids:
            return {}
        rows = (await session.execute(select(AppUser.user_id, AppUser.name).where(AppUser.user_id.in_(ids)))).all()
        return {int(uid): name for uid, name in rows}

    async def list_alerts(self, caller: Caller, f: AlertFilters) -> Dict[str, Any]:
        """GET /alerts. The checks run in the stub's order, so a request with several errors gets the same one."""
        require_tenant(caller)
        page, size = page_params(f.page, f.size)
        if f.sort not in ("createdAt", "severity"):
            raise Validation("sort must be createdAt or severity", {"field": "sort"})
        stmt = scope(select(Alert), caller)
        statuses = parse_statuses(f.status)
        if statuses:
            stmt = stmt.where(Alert.status.in_(statuses))
        if f.type:
            stmt = stmt.where(Alert.alert_type_code == f.type.upper())
        if f.severity:
            stmt = stmt.where(Alert.severity == f.severity.upper())
        if f.vehicle_id:
            stmt = stmt.where(Alert.vehicle_id == f.vehicle_id)
        if f.from_:
            stmt = stmt.where(Alert.created_at >= parse_iso(f.from_, "from"))
        if f.to:
            stmt = stmt.where(Alert.created_at < parse_iso(f.to, "to"))
        # Newest first; by severity: highest first, then newest. alert_id breaks ties, so pages never overlap.
        order = [Alert.created_at.desc(), Alert.alert_id.desc()]
        if f.sort == "severity":
            rank = case(*((Alert.severity == s, r) for s, r in SEVERITY_RANK.items()), else_=0)
            order.insert(0, rank.desc())
        async with self.session_factory() as session:
            total = int((await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one())
            rows = list((await session.execute(stmt.order_by(*order).offset((page - 1) * size).limit(size)))
                        .scalars().all())
            names = await self._user_names(session, [r.acknowledged_by for r in rows] + [r.closed_by for r in rows])
        now = utcnow()
        return {"items": [alert_row(r, names, now) for r in rows], "total": total, "page": page, "size": size}

    async def map_points(self, caller: Caller, status: Optional[str]) -> List[Dict[str, Any]]:
        """GET /alerts/map: the newest 500 matching alerts that have a position, newest first."""
        require_tenant(caller)
        statuses = parse_statuses(status) or list(OPEN_STATUSES)
        stmt = scope(select(Alert).where(Alert.status.in_(statuses)), caller)
        async with self.session_factory() as session:
            rows = list((await session.execute(stmt.order_by(Alert.created_at.desc()).limit(MAP_LIMIT))).scalars().all())
        # 1. the alert row; 2. the audio event (only for rows without a position); 3. the car's newest telemetry.
        need_event = [r.event_ref for r in rows if r.lat is None or r.lon is None]
        events = await self.events.many(need_event, caller.tenant_code)
        positions: Optional[Dict[str, Tuple[float, float]]] = None
        points = []
        for r in rows:
            where = (r.lat, r.lon) if r.lat is not None and r.lon is not None else location_of(events.get(r.event_ref))
            if where is None:
                if positions is None:
                    positions = await self.events.latest_positions(caller.tenant_code)
                where = positions.get(r.vehicle_id)
            if where is None:
                continue
            points.append({"alertId": r.alert_id, "vehicleId": r.vehicle_id, "lat": where[0], "lon": where[1],
                           "severity": r.severity, "type": r.alert_type_code, "status": r.status,
                           "topClass": sound_class_name(r.top_class_id), "createdAt": iso(as_naive_utc(r.created_at))})
        return points

    async def get_detail(self, caller: Caller, alert_id: int) -> Dict[str, Any]:
        """GET /alerts/{alertId}: 404 when the alert doesn't exist or is outside the caller's scope."""
        async with self.session_factory() as session:
            row = (await session.execute(scope(select(Alert).where(Alert.alert_id == alert_id), caller))).scalars().first()
            if row is None:
                raise NotFound("Alert not found")
            actions = list((await session.execute(select(AlertAction).where(AlertAction.alert_id == alert_id)
                                                  .order_by(AlertAction.ts, AlertAction.action_id))).scalars().all())
            rule = await session.get(AlertRule, row.rule_id)
            names = await self._user_names(session, [row.acknowledged_by, row.closed_by] + [a.user_id for a in actions])
        tenant_code = await self.tenants.code_of(row.tenant_id)  # the alert's own tenant, also for staff
        doc = await self.events.get(row.event_ref, tenant_code)
        return {"alert": alert_row(row, names), "event": event_info(doc) if doc else None, "rule": rule_info(rule),
                "clip": clip_info(doc), "actions": [action_entry(a, names) for a in actions],
                "notifications": []}  # filled from the notification table in step 8
