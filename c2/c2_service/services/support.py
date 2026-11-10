"""Small pieces shared by the C2 services: the response shapes (the ones stubs/c2.py returns,
plus the C2 fields), C1 calls, the audit row, and lookups by public code with the tenant scope.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

import httpx
from sqlalchemy import select

from ..auth import Caller
from ..errors import NotFound
from ..models import AuditLog, Device, Vehicle
from ..timeutil import as_naive_utc, iso, utcnow

log = logging.getLogger("scicp.c2")


# -- lookups --------------------------------------------------------------------------------------
async def vehicle_in_scope(session, caller: Caller, code: str, include_retired: bool = True) -> Vehicle:
    """The caller's vehicle by code; 404 (never 403) for another tenant's car (Section 2.10)."""
    stmt = select(Vehicle).where(Vehicle.vehicle_code == code)
    if caller.tenant_id is not None:
        stmt = stmt.where(Vehicle.tenant_id == caller.tenant_id)
    rows = list((await session.execute(stmt)).scalars().all())
    if not rows:
        raise NotFound("Vehicle not found")
    if len(rows) > 1:  # the same code in two tenants: staff must pick the tenant
        raise NotFound("Vehicle not found; pass tenantId")
    vehicle = rows[0]
    if caller.rider_vehicle_codes is not None and code not in caller.rider_vehicle_codes:
        raise NotFound("Vehicle not found")
    if not include_retired and vehicle.status == "RETIRED":
        raise NotFound("Vehicle not found")
    return vehicle


async def device_in_scope(session, caller: Caller, code: str) -> Device:
    stmt = select(Device).where(Device.device_code == code, Device.deleted_at.is_(None))
    if caller.tenant_id is not None:
        stmt = stmt.where(Device.tenant_id == caller.tenant_id)
    device = (await session.execute(stmt)).scalars().first()
    if device is None:
        raise NotFound("Device not found")
    if caller.rider_vehicle_codes is not None:
        vehicle = await session.get(Vehicle, device.vehicle_id)
        if vehicle is None or vehicle.vehicle_code not in caller.rider_vehicle_codes:
            raise NotFound("Device not found")
    return device


async def devices_of(session, vehicle_ids: Iterable[int], include_deleted: bool = False) -> Dict[int, List[Device]]:
    ids = list(vehicle_ids)
    out: Dict[int, List[Device]] = {i: [] for i in ids}
    if not ids:
        return out
    stmt = select(Device).where(Device.vehicle_id.in_(ids)).order_by(Device.device_code)
    if not include_deleted:
        stmt = stmt.where(Device.deleted_at.is_(None))
    for d in (await session.execute(stmt)).scalars():
        out.setdefault(d.vehicle_id, []).append(d)
    return out


# -- response shapes ------------------------------------------------------------------------------
def gateway_of(devices: List[Device]) -> Optional[Device]:
    return next((d for d in devices if d.device_type == "EDGE_GATEWAY"), None)


def connection_of(devices: List[Device]) -> str:
    """ONLINE or OFFLINE from the gateway's status (Section 4.5); NEVER when the car never sent anything."""
    gw = gateway_of(devices)
    if gw is None or gw.last_seen_at is None:
        return "NEVER"
    return "ONLINE" if gw.status in ("ONLINE", "FAULT") else "OFFLINE"


def device_summary(devices: List[Device]) -> Dict[str, int]:
    counts = {"total": len(devices), "online": 0, "offline": 0, "fault": 0, "disabled": 0}
    for d in devices:
        key = d.status.lower()
        if key in counts:
            counts[key] += 1
    return counts


def device_dto(d: Device, vehicle_code: str) -> Dict[str, Any]:
    """The stub's fields (deviceId, type, status, lastSeenAt, vehicleId, muted, firmware, faultReason)
    plus position, serialNo and the configuration state (Table 7 of the C2 design)."""
    out: Dict[str, Any] = {
        "deviceId": d.device_code, "type": d.device_type, "status": d.status, "lastSeenAt": iso(d.last_seen_at),
        "vehicleId": vehicle_code, "muted": bool(d.muted), "firmware": d.firmware_version,
        "firmwareVersion": d.firmware_version, "position": d.position, "serialNo": d.serial_no,
    }
    if d.fault_reason and d.status == "FAULT":
        out["faultReason"] = d.fault_reason
    if d.device_type in ("MIC_INTERIOR", "MIC_EXTERIOR"):
        out["config"] = {"version": d.config_version, "state": d.config_state, "values": d.config_json,
                         "pending": d.desired_config_json if d.config_state in ("PENDING", "TIMED_OUT", "REJECTED") else None}
    if d.deleted_at is not None:
        out["deletedAt"] = iso(d.deleted_at)
    return out


def vehicle_dto(v: Vehicle, devices: List[Device], sample: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Exactly the stub's vehicle fields, so C1's overview and vehicle pages read C2 unchanged."""
    gw = gateway_of(devices)
    coords = ((sample or {}).get("location") or {}).get("coordinates") or [None, None]
    lat, lon = (coords[1], coords[0]) if coords and coords[0] is not None else (None, None)
    return {"vehicleId": v.vehicle_code, "vin": v.vin, "label": v.label, "make": v.make, "model": v.model,
            "year": v.model_year, "status": v.status, "connection": connection_of(devices),
            "lastSeenAt": iso(gw.last_seen_at if gw else None), "retentionDays": v.retention_days,
            "deviceSummary": device_summary(devices), "tenantId": v.tenant_id, "iotThingName": v.iot_thing_name,
            "position": {"lat": lat, "lon": lon} if lat is not None else None,
            "provisionedAt": iso(v.provisioned_at), "createdAt": iso(v.created_at), "updatedAt": iso(v.updated_at)}


def telemetry_dto(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {"ts": iso(as_naive_utc(doc.get("ts"))), "speedKph": doc.get("speedKph"), "driveMode": doc.get("driveMode"),
            "ignition": doc.get("ignition"), "location": doc.get("location"), "network": doc.get("network"),
            "agent": doc.get("agent"), "devices": doc.get("devices")}


def status_payload(d: Device, vehicle_code: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """device.status as C1's WebSocket table lists it: {vehicleId, deviceId, status, lastSeenAt, faultReason?,
    muted?, config?} (tenantId is added by the publisher)."""
    payload: Dict[str, Any] = {"vehicleId": vehicle_code, "deviceId": d.device_code, "status": d.status,
                               "lastSeenAt": iso(d.last_seen_at), "muted": bool(d.muted)}
    if d.status == "FAULT" and d.fault_reason:
        payload["faultReason"] = d.fault_reason
    if config is not None:
        payload["config"] = config
    return payload


async def latest_samples(mongo: Any, tenant_code: Optional[str], vehicle_codes: Optional[List[str]] = None
                         ) -> Dict[str, Dict[str, Any]]:
    match: Dict[str, Any] = {}
    if tenant_code is not None:
        match["meta.tenantId"] = tenant_code
    if vehicle_codes is not None:
        match["meta.vehicleId"] = {"$in": list(vehicle_codes)}
    pipeline = [{"$match": match}, {"$sort": {"ts": -1}},
                {"$group": {"_id": "$meta.vehicleId", "latest": {"$first": "$$ROOT"}}}]
    out: Dict[str, Dict[str, Any]] = {}
    for doc in await mongo["vehicle_telemetry"].aggregate(pipeline).to_list(None):
        out[doc["_id"]] = doc["latest"]
    return out


# -- audit (C1's AuditLogger row) and C1 calls ---------------------------------------------------------
def audit(session, caller: Optional[Caller], tenant_id: Optional[int], action: str, entity: str, entity_id: str,
          before: Optional[dict] = None, after: Optional[dict] = None) -> None:
    session.add(AuditLog(actor_user_id=caller.user_id if caller else None, tenant_id=tenant_id, action=action,
                         entity=entity, entity_id=entity_id, before_json=before, after_json=after, ts=utcnow()))


class C1Client:
    """POST /internal/subscriptions/defaults (the vehicle-create hook of the C1 design)."""

    def __init__(self, base_url: str, token_source, timeout: float = 5.0, transport=None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token_source = token_source
        self.timeout = timeout
        self.transport = transport

    async def subscription_defaults(self, vehicle_code: str, tenant_id: int) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                r = await client.post(f"{self.base_url}/internal/subscriptions/defaults",
                                      json={"vehicleId": vehicle_code, "tenantId": tenant_id},
                                      headers={"Authorization": f"Bearer {self.token_source()}"})
            if r.status_code >= 400:
                log.warning("C1 refused subscription defaults for %s: %s %s", vehicle_code, r.status_code, r.text[:200])
                return False
            return True
        except httpx.HTTPError as exc:
            log.warning("C1 unreachable for subscription defaults of %s: %s", vehicle_code, exc)
            return False


def seconds_since(ts: Optional[datetime], now: Optional[datetime] = None) -> Optional[float]:
    if ts is None:
        return None
    return ((now or utcnow()) - as_naive_utc(ts)).total_seconds()
