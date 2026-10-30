"""C3 stub: the alert queue, alert detail and actions, the map points and
the alert rules (Part III, Tables 83 and 84), served from the seeded alert
and alert_action tables and the audio_events collection.

Mounted at /c3/api/v1 (inline) or /api/v1 on :8003 (standalone).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.timeutil import iso, parse_iso
from app.errors import Conflict, Forbidden, NotFound, Validation
from app.models import OPEN_STATUSES, Alert, AlertAction, AppUser, Subscription, Vehicle, sound_class_name

from .common import (ALERT_RULES, RULE_BY_ID, Caller, StubContext, as_naive_utc, forbid_roles, get_ctx, latest_samples,
                     page_params, resolve_caller, sample_position, user_names, utcnow)

log = logging.getLogger("scicp.stubs.c3")

c3_router = APIRouter(tags=["c3-stub"])

STATUS_FILTERS = {"NEW", "ACKNOWLEDGED", "RESOLVED", "FALSE_ALARM", "OPEN", "CLOSED"}
# BR-C3-07: allowed transitions per action: (from statuses, to status)
TRANSITIONS = {
    "ACKNOWLEDGE": (("NEW",), "ACKNOWLEDGED"),
    "RESOLVE": (("NEW", "ACKNOWLEDGED"), "RESOLVED"),
    "FALSE_ALARM": (("NEW", "ACKNOWLEDGED"), "FALSE_ALARM"),
}
FEEDBACK_OF_ACTION = {"RESOLVE": "CONFIRMED", "FALSE_ALARM": "FALSE_ALARM"}
SEVERITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}


class ActionBody(BaseModel):
    action: Literal["ACKNOWLEDGE", "RESOLVE", "FALSE_ALARM"]
    note: Optional[str] = Field(default=None, max_length=500)


async def _caller(request: Request, tenantId: Optional[str] = Query(default=None),
                  ctx: StubContext = Depends(get_ctx)) -> Caller:
    caller = await resolve_caller(request, ctx, tenantId, require_tenant=False)
    forbid_roles(caller, "IOT_ENGINEER")  # BR-01 of C1: no alert access for the IoT engineer
    return caller


def _statuses(value: Optional[str]) -> Optional[List[str]]:
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


def alert_dto(row: Alert, names: Dict[int, str], now: Optional[Any] = None) -> Dict[str, Any]:
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


def _scope_filter(stmt, caller: Caller):
    if caller.tenant_id is not None:
        stmt = stmt.where(Alert.tenant_id == caller.tenant_id)
    if caller.rider_vehicle_codes is not None:
        stmt = stmt.where(Alert.vehicle_id.in_(caller.rider_vehicle_codes or ["-"]))
    return stmt


async def _event_doc(ctx: StubContext, event_ref: Optional[str]) -> Optional[Dict[str, Any]]:
    if not event_ref:
        return None
    try:
        oid = ObjectId(event_ref)
    except (InvalidId, TypeError):
        return None
    return await ctx.mongo["audio_events"].find_one({"_id": oid})


def _event_dto(doc: Dict[str, Any]) -> Dict[str, Any]:
    inference = doc.get("inference") or {}
    return {"eventRef": str(doc.get("_id")), "eventId": doc.get("eventId"), "ts": iso(as_naive_utc(doc.get("ts"))),
            "channel": doc.get("channel"), "deviceId": doc.get("deviceId"), "location": doc.get("location"),
            "context": doc.get("context"), "topK": inference.get("topK"), "modelVersion": inference.get("modelVersion"),
            "latencyMs": inference.get("latencyMs"), "edge": doc.get("edge"), "decision": doc.get("decision")}


def _rule_dto(rule_id: Optional[int]) -> Optional[Dict[str, Any]]:
    rule = RULE_BY_ID.get(rule_id)
    if rule is None:
        return None
    return {"ruleId": rule["ruleId"], "scope": rule["scope"], "alertType": rule["alertType"], "class": rule["class"],
            "minConfidence": rule["minConfidence"], "vehicleState": rule["vehicleState"]}


# -------------------------------------------------------------------------------------------
# GET /alerts
# -------------------------------------------------------------------------------------------
@c3_router.get("/alerts")
async def list_alerts(status: Optional[str] = None, type: Optional[str] = None, severity: Optional[str] = None,
                      vehicleId: Optional[str] = None, from_: Optional[str] = Query(default=None, alias="from"),
                      to: Optional[str] = None, sort: str = "createdAt", page: Optional[int] = None,
                      size: Optional[int] = None, limit: Optional[int] = None,
                      caller: Caller = Depends(_caller), ctx: StubContext = Depends(get_ctx)):
    """{items[{alertId, vehicleId, type, severity, status, topClass, confidence, createdAt, ageSeconds,
    acknowledgedBy, lat, lon}], total, page, size}; status OPEN = NEW or ACKNOWLEDGED."""
    if caller.tenant_id is None:
        raise Validation("tenantId is required", {"field": "tenantId"})
    page, size = page_params(page, size or limit)
    if sort not in ("createdAt", "severity"):
        raise Validation("sort must be createdAt or severity", {"field": "sort"})
    stmt = _scope_filter(select(Alert), caller)
    statuses = _statuses(status)
    if statuses:
        stmt = stmt.where(Alert.status.in_(statuses))
    if type:
        stmt = stmt.where(Alert.alert_type_code == type.upper())
    if severity:
        stmt = stmt.where(Alert.severity == severity.upper())
    if vehicleId:
        stmt = stmt.where(Alert.vehicle_id == vehicleId)
    if from_:
        stmt = stmt.where(Alert.created_at >= parse_iso(from_, "from"))
    if to:
        stmt = stmt.where(Alert.created_at < parse_iso(to, "to"))
    async with ctx.session_factory() as session:
        total = int((await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one())
        rows = list((await session.execute(stmt.order_by(Alert.created_at.desc(), Alert.alert_id.desc()))).scalars().all())
        if sort == "severity":
            rows.sort(key=lambda r: (SEVERITY_RANK.get(r.severity, 0), r.created_at), reverse=True)
        start = (page - 1) * size
        rows = rows[start:start + size]
        names = await user_names(session, [r.acknowledged_by for r in rows] + [r.closed_by for r in rows])
    now = utcnow()
    return {"items": [alert_dto(r, names, now) for r in rows], "total": total, "page": page, "size": size}


# -------------------------------------------------------------------------------------------
# GET /alerts/map  (declared before /alerts/{alert_id} so that "map" is not parsed as an id)
# -------------------------------------------------------------------------------------------
@c3_router.get("/alerts/map")
async def alerts_map(status: Optional[str] = "OPEN", caller: Caller = Depends(_caller),
                     ctx: StubContext = Depends(get_ctx)):
    """[{alertId, vehicleId, lat, lon, severity, type, createdAt}] - the event's location, else the
    alert row's, else the car's newest telemetry position."""
    if caller.tenant_id is None:
        raise Validation("tenantId is required", {"field": "tenantId"})
    statuses = _statuses(status) or list(OPEN_STATUSES)
    stmt = _scope_filter(select(Alert).where(Alert.status.in_(statuses)), caller)
    async with ctx.session_factory() as session:
        rows = list((await session.execute(stmt.order_by(Alert.created_at.desc()).limit(500))).scalars().all())
    refs = []
    for r in rows:
        try:
            refs.append(ObjectId(r.event_ref))
        except (InvalidId, TypeError):
            continue
    events = {}
    if refs:
        for doc in await ctx.mongo["audio_events"].find({"_id": {"$in": refs}}).to_list(None):
            events[str(doc["_id"])] = doc
    samples = None
    points = []
    for r in rows:
        lat, lon = None, None
        doc = events.get(r.event_ref)
        if doc and (doc.get("location") or {}).get("coordinates"):
            lon, lat = doc["location"]["coordinates"][:2]
        if lat is None and r.lat is not None and r.lon is not None:
            lat, lon = r.lat, r.lon
        if lat is None:
            if samples is None:
                samples = await latest_samples(ctx.mongo, caller.tenant_code)
            lat, lon = sample_position(samples.get(r.vehicle_id))
        if lat is None:
            continue
        points.append({"alertId": r.alert_id, "vehicleId": r.vehicle_id, "lat": lat, "lon": lon, "severity": r.severity,
                       "type": r.alert_type_code, "status": r.status, "topClass": sound_class_name(r.top_class_id),
                       "createdAt": iso(as_naive_utc(r.created_at))})
    return points


# -------------------------------------------------------------------------------------------
# GET /alerts/{alertId}
# -------------------------------------------------------------------------------------------
async def _load_alert(session, caller: Caller, alert_id: int) -> Alert:
    row = (await session.execute(_scope_filter(select(Alert).where(Alert.alert_id == alert_id), caller))).scalars().first()
    if row is None:
        raise NotFound("Alert not found")
    return row


async def _notifications(session, row: Alert) -> List[Dict[str, Any]]:
    """What C3's notifier would have sent: one entry per channel of the vehicle's subscription row."""
    vehicle = (await session.execute(select(Vehicle).where(Vehicle.tenant_id == row.tenant_id,
                                                          Vehicle.vehicle_code == row.vehicle_id))).scalars().first()
    if vehicle is None:
        return []
    sub = (await session.execute(select(Subscription).where(
        Subscription.vehicle_id == vehicle.vehicle_id, Subscription.service_code == row.alert_type_code))).scalars().first()
    if sub is None or not sub.enabled:
        return [{"recipient": "-", "channel": "NONE", "status": "SUPPRESSED", "reason": "service not subscribed"}]
    recipients = (await session.execute(select(AppUser.email).where(
        AppUser.tenant_id == row.tenant_id, AppUser.role.in_(("OWNER", "FLEET_MANAGER")),
        AppUser.status == "ACTIVE"))).scalars().all()
    out = []
    for channel in [c.strip() for c in (sub.channels or "").split(",") if c.strip()]:
        for email in recipients:
            out.append({"recipient": email, "channel": channel, "status": "SENT",
                        "sentAt": iso(as_naive_utc(row.created_at))})
    return out


@c3_router.get("/alerts/{alert_id}")
async def get_alert(alert_id: int, caller: Caller = Depends(_caller), ctx: StubContext = Depends(get_ctx)):
    """{alert, event{eventRef, ts, channel, location, context, topK, modelVersion, latencyMs}, rule, clip,
    actions[{action, userId, name, by, note, ts, at}], notifications[{recipient, channel, status}]}"""
    async with ctx.session_factory() as session:
        row = await _load_alert(session, caller, alert_id)
        actions = list((await session.execute(select(AlertAction).where(AlertAction.alert_id == alert_id)
                                              .order_by(AlertAction.ts, AlertAction.action_id))).scalars().all())
        names = await user_names(session, [row.acknowledged_by, row.closed_by] + [a.user_id for a in actions])
        notifications = await _notifications(session, row)
    doc = await _event_doc(ctx, row.event_ref)
    event = _event_dto(doc) if doc else None
    clip = None
    if doc and doc.get("clip"):
        clip_info = doc["clip"]
        # The clip bucket belongs to C2; without it the player stays hidden (url null).
        clip = {"clipId": clip_info.get("clipId"), "s3Key": clip_info.get("s3Key"), "url": None, "expiresAt": None,
                "durationMs": clip_info.get("durationMs"), "sampleRate": clip_info.get("sampleRate"),
                "note": "clip storage is served by the C2 component"}
    return {
        "alert": alert_dto(row, names),
        "event": event,
        "rule": _rule_dto(row.rule_id),
        "clip": clip,
        "actions": [{"action": a.action, "userId": a.user_id, "name": names.get(a.user_id) if a.user_id else None,
                     "by": names.get(a.user_id) if a.user_id else ("system" if a.action == "CREATE" else None),
                     "fromStatus": a.from_status, "toStatus": a.to_status, "note": a.note,
                     "ts": iso(as_naive_utc(a.ts)), "at": iso(as_naive_utc(a.ts))} for a in actions],
        "notifications": notifications,
    }


# -------------------------------------------------------------------------------------------
# POST /alerts/{alertId}/actions
# -------------------------------------------------------------------------------------------
@c3_router.post("/alerts/{alert_id}/actions")
async def post_action(alert_id: int, body: ActionBody, caller: Caller = Depends(_caller),
                      ctx: StubContext = Depends(get_ctx)):
    """NEW -ACKNOWLEDGE-> ACKNOWLEDGED -RESOLVE/FALSE_ALARM-> RESOLVED/FALSE_ALARM (BR-C3-07, 409 otherwise).
    Writes alert_action, publishes alert.updated {alertId, status, by}, and on RESOLVE / FALSE_ALARM records
    the feedback label on C1 (CONFIRMED / FALSE_ALARM) with the caller's token."""
    if caller.role in ("RIDER", "IOT_ENGINEER"):
        raise Forbidden()
    allowed_from, to_status = TRANSITIONS[body.action]
    now = utcnow()
    async with ctx.session_factory() as session:
        row = await _load_alert(session, caller, alert_id)
        if row.status not in allowed_from:
            raise Conflict("Action not allowed in this state",
                           {"rule": "BR-C3-07", "from": row.status, "to": to_status,
                            "allowed": [a for a, (froms, _) in TRANSITIONS.items() if row.status in froms]})
        from_status = row.status
        row.status = to_status
        row.updated_at = now
        if body.action == "ACKNOWLEDGE" or row.acknowledged_at is None:
            row.acknowledged_by = caller.user_id
            row.acknowledged_at = now
        if to_status in ("RESOLVED", "FALSE_ALARM"):
            row.closed_by = caller.user_id
            row.closed_at = now
        session.add(AlertAction(alert_id=alert_id, user_id=caller.user_id, action=body.action, from_status=from_status,
                                to_status=to_status, note=body.note, ts=now))
        await session.commit()
        names = await user_names(session, [row.acknowledged_by, row.closed_by])
        by = names.get(caller.user_id) if caller.user_id is not None else caller.name
        dto = alert_dto(row, names, now)
        tenant_id, event_ref = row.tenant_id, row.event_ref
    await ctx.publisher.publish("alert.updated", {"tenantId": tenant_id, "alertId": alert_id, "status": to_status,
                                                  "by": by, "action": body.action, "vehicleId": dto["vehicleId"]})
    label = FEEDBACK_OF_ACTION.get(body.action)
    if label:
        await ctx.publisher.feedback(event_ref, label, caller)
    return dto


# -------------------------------------------------------------------------------------------
# GET /alert-rules
# -------------------------------------------------------------------------------------------
@c3_router.get("/alert-rules")
async def alert_rules(caller: Caller = Depends(_caller)):
    """The effective rule list (Table 84): the platform defaults of Table 95; no tenant override in the demo."""
    if caller.role in ("OWNER", "RIDER"):
        raise Forbidden()
    rules = []
    for rule in ALERT_RULES:
        entry = dict(rule)
        entry["soundClass"] = rule["class"]
        entry["updatedBy"] = None
        entry["updatedAt"] = None
        rules.append(entry)
    return rules


__all__ = ["c3_router", "alert_dto"]
