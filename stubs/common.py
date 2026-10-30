"""Pieces shared by the C2 and C3 stub routers and the demo generator:
the runtime context (databases, secrets, publisher), caller resolution
(user JWT or service token, tenant scope), the Publisher abstraction
(inline EventBus or HTTP to C1), and small data helpers.

The stubs reuse the SQLAlchemy models and the error envelope of the C1 API
package (c1/api/app) so that the rows they read and write are exactly the
ones the dashboard shows.
"""
from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import jwt
from fastapi import Request
from sqlalchemy import select

_API_DIR = Path(__file__).resolve().parents[1] / "c1" / "api"
try:  # the C1 API package: present on PYTHONPATH in the API process and in the Docker images
    import app  # noqa: F401
except ImportError:  # developer checkout: resolve it relative to this file
    sys.path.insert(0, str(_API_DIR))

from app.core.timeutil import iso  # noqa: E402
from app.errors import Forbidden, Unauthorized, Validation  # noqa: E402
from app.models import STAFF_ROLES, AppUser, Tenant, Vehicle, VehicleRider  # noqa: E402

log = logging.getLogger("scicp.stubs")

ONLINE_WINDOW = timedelta(seconds=30)
PAGE_SIZE_DEFAULT = 25
PAGE_SIZE_MAX = 100

# Default alert rules of Part III, Table 95 (tenant_id null). rule_id = position + 1.
ALERT_RULES: List[Dict[str, Any]] = [
    {"ruleId": 1, "scope": "DEFAULT", "tenantId": None, "alertType": "EMERGENCY", "class": "siren",
     "vehicleState": "ANY", "minConfidence": 0.60, "severity": "HIGH", "severityOverride": None,
     "minRepeats": 1, "windowSeconds": None, "enabled": True},
    {"ruleId": 2, "scope": "DEFAULT", "tenantId": None, "alertType": "ACCIDENT", "class": "crash",
     "vehicleState": "MOVING", "minConfidence": 0.70, "severity": "CRITICAL", "severityOverride": None,
     "minRepeats": 1, "windowSeconds": None, "enabled": True},
    {"ruleId": 3, "scope": "DEFAULT", "tenantId": None, "alertType": "ACCIDENT", "class": "glass_break",
     "vehicleState": "MOVING", "minConfidence": 0.70, "severity": "CRITICAL", "severityOverride": None,
     "minRepeats": 1, "windowSeconds": None, "enabled": True},
    {"ruleId": 4, "scope": "DEFAULT", "tenantId": None, "alertType": "SECURITY", "class": "glass_break",
     "vehicleState": "PARKED", "minConfidence": 0.70, "severity": "HIGH", "severityOverride": None,
     "minRepeats": 1, "windowSeconds": None, "enabled": True},
    {"ruleId": 5, "scope": "DEFAULT", "tenantId": None, "alertType": "SECURITY", "class": "scream",
     "vehicleState": "PARKED", "minConfidence": 0.60, "severity": "HIGH", "severityOverride": None,
     "minRepeats": 1, "windowSeconds": None, "enabled": True},
    {"ruleId": 6, "scope": "DEFAULT", "tenantId": None, "alertType": "EMERGENCY", "class": "scream",
     "vehicleState": "MOVING", "minConfidence": 0.60, "severity": "HIGH", "severityOverride": None,
     "minRepeats": 1, "windowSeconds": None, "enabled": True},
    {"ruleId": 7, "scope": "DEFAULT", "tenantId": None, "alertType": "SAFETY", "class": "honking",
     "vehicleState": "ANY", "minConfidence": 0.60, "severity": "MEDIUM", "severityOverride": None,
     "minRepeats": 2, "windowSeconds": 10, "enabled": True},
]
RULE_BY_ID = {r["ruleId"]: r for r in ALERT_RULES}
SEVERITY_OF_TYPE = {"ACCIDENT": "CRITICAL", "EMERGENCY": "HIGH", "SECURITY": "HIGH", "SAFETY": "MEDIUM",
                    "PASSENGER": "LOW", "VEHICLE_HEALTH": "LOW"}

# Three devices per car: the edge gateway and the two microphones (Part II device types).
DEVICE_TYPES = (("10", "EDGE_GATEWAY"), ("11", "MIC_INTERIOR"), ("12", "MIC_EXTERIOR"))
# Live device state changed by the demo generator: deviceId -> {status, faultReason, until}
DEVICE_OVERRIDES: Dict[str, Dict[str, Any]] = {}


# -------------------------------------------------------------------------------------------
# time helpers
# -------------------------------------------------------------------------------------------
def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_naive_utc(value: Any) -> Optional[datetime]:
    """MongoDB drivers may hand back aware datetimes; the SQL rows are naive UTC."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def connection_of(sample_ts: Optional[datetime], now: Optional[datetime] = None) -> str:
    """ONLINE when the newest telemetry sample is younger than 30 s, OFFLINE when older, NEVER without one."""
    if sample_ts is None:
        return "NEVER"
    now = now or utcnow()
    return "ONLINE" if now - sample_ts < ONLINE_WINDOW else "OFFLINE"


def page_params(page: Optional[int], size: Optional[int]) -> tuple:
    page = page or 1
    size = size or PAGE_SIZE_DEFAULT
    if page < 1:
        raise Validation("page must be 1 or more", {"field": "page"})
    if size < 1 or size > PAGE_SIZE_MAX:
        raise Validation(f"size must be between 1 and {PAGE_SIZE_MAX}", {"field": "size", "max": PAGE_SIZE_MAX})
    return page, size


# -------------------------------------------------------------------------------------------
# Publisher: inline (EventBus) or HTTP (POST /internal/events on C1)
# -------------------------------------------------------------------------------------------
class InlinePublisher:
    """Runs inside the C1 process: publishes on the EventBus and records feedback through the
    registry service directly."""

    def __init__(self, bus: Any, container: Any = None) -> None:
        self.bus = bus
        self.container = container

    async def publish(self, topic: str, payload: Dict[str, Any]) -> None:
        await self.bus.publish(topic, payload)

    async def feedback(self, event_ref: str, label: str, caller: "Caller") -> None:
        if self.container is None or caller.user_id is None:
            return
        try:
            from app.schemas import FeedbackBody
            from app.services import ModelRegistryService
            async with self.container.session_factory() as session:
                await ModelRegistryService(self.container, session).record_feedback(
                    event_ref, FeedbackBody(label=label), caller.user_id, caller.role, caller.claim_tenant_id, None)
        except Exception as exc:  # noqa: BLE001 - feedback is best effort (BR-11 duplicates, unknown event)
            log.info("feedback %s on %s not recorded: %s", label, event_ref, exc)


class HttpPublisher:
    """Runs in the stubs container: POST /internal/events with the service token, and
    POST /events/{eventRef}/feedback with the user's own token."""

    def __init__(self, c1_base_url: str, service_token_secret: str, timeout: float = 5.0) -> None:
        self.base_url = c1_base_url.rstrip("/")
        self.secret = service_token_secret
        self.timeout = timeout
        self._token: Optional[str] = None
        self._token_exp = 0

    def service_token(self) -> str:
        now = int(time.time())
        if self._token is None or now > self._token_exp - 300:
            self._token_exp = now + 24 * 3600
            self._token = jwt.encode({"sub": "stubs", "role": "SERVICE", "iat": now, "exp": self._token_exp,
                                      "jti": uuid.uuid4().hex}, self.secret, algorithm="HS256")
        return self._token

    async def publish(self, topic: str, payload: Dict[str, Any]) -> None:
        body = {"topic": topic, "payload": payload, "tenantId": payload.get("tenantId")}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/internal/events", json=body,
                                             headers={"Authorization": f"Bearer {self.service_token()}"})
            if response.status_code >= 400:
                log.warning("C1 refused %s: %s %s", topic, response.status_code, response.text[:200])
        except httpx.HTTPError as exc:
            log.warning("C1 unreachable for %s: %s", topic, exc)

    async def feedback(self, event_ref: str, label: str, caller: "Caller") -> None:
        if caller.is_service:
            return
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/events/{event_ref}/feedback", json={"label": label},
                                             headers={"Authorization": f"Bearer {caller.token}"})
            if response.status_code >= 400:
                log.info("feedback %s on %s not recorded: %s", label, event_ref, response.status_code)
        except httpx.HTTPError as exc:
            log.info("feedback %s on %s not sent: %s", label, event_ref, exc)


# -------------------------------------------------------------------------------------------
# Context and caller
# -------------------------------------------------------------------------------------------
class StubContext:
    """Long-lived objects of one stub process (or of the inline mount)."""

    def __init__(self, session_factory: Any, mongo: Any, jwt_secret: str, service_token_secret: str,
                 publisher: Any) -> None:
        self.session_factory = session_factory
        self.mongo = mongo
        self.jwt_secret = jwt_secret
        self.service_token_secret = service_token_secret
        self.publisher = publisher
        self._tenant_codes: Dict[int, str] = {}

    @classmethod
    def from_container(cls, container: Any) -> "StubContext":
        settings = container.settings
        return cls(container.session_factory, container.mongo, settings.jwt_secret, settings.service_token_secret,
                   InlinePublisher(container.bus, container))

    async def tenant_code(self, tenant_id: Optional[int]) -> Optional[str]:
        if tenant_id is None:
            return None
        if tenant_id not in self._tenant_codes:
            async with self.session_factory() as session:
                code = (await session.execute(select(Tenant.code).where(Tenant.tenant_id == tenant_id))).scalar_one_or_none()
            if code is None:
                return None
            self._tenant_codes[tenant_id] = code
        return self._tenant_codes[tenant_id]

    async def tenant_id_of(self, code: str) -> Optional[int]:
        for tid, c in self._tenant_codes.items():
            if c == code:
                return tid
        async with self.session_factory() as session:
            tid = (await session.execute(select(Tenant.tenant_id).where(Tenant.code == code))).scalar_one_or_none()
        if tid is not None:
            self._tenant_codes[tid] = code
        return tid


def get_ctx(request: Request) -> StubContext:
    """The StubContext of this app: built once from the C1 container in inline mode."""
    ctx = getattr(request.app.state, "stub_ctx", None)
    if ctx is None:
        container = getattr(request.app.state, "container", None)
        if container is None:
            raise RuntimeError("stub context missing: neither stub_ctx nor a C1 container on app.state")
        ctx = StubContext.from_container(container)
        request.app.state.stub_ctx = ctx
    return ctx


@dataclass
class Caller:
    """Who calls: a platform user (JWT) or a component (service token), with the tenant scope."""
    role: str
    user_id: Optional[int]
    name: str
    claim_tenant_id: Optional[int]   # tenant of the token (None for staff and service)
    tenant_id: Optional[int]         # scope of this request (token tenant, or the tenantId parameter)
    tenant_code: Optional[str]
    token: str
    is_service: bool = False
    rider_vehicle_codes: Optional[List[str]] = None  # RIDER: cars of the active trip

    @property
    def is_staff(self) -> bool:
        return self.is_service or self.role in STAFF_ROLES


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise Unauthorized("Missing bearer token")
    return header[7:].strip()


def _decode(token: str, ctx: StubContext) -> Dict[str, Any]:
    try:
        payload = jwt.decode(token, ctx.jwt_secret, algorithms=["HS256"])
        if payload.get("role") == "SERVICE":
            raise Unauthorized("Invalid token")
        return payload
    except jwt.PyJWTError:
        pass
    try:
        payload = jwt.decode(token, ctx.service_token_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise Unauthorized("Invalid token")
    if payload.get("role") != "SERVICE":
        raise Unauthorized("Invalid token")
    return payload


async def resolve_caller(request: Request, ctx: StubContext, tenant_param: Optional[str],
                         require_tenant: bool = True) -> Caller:
    """Tenant roles are scoped to the tenant of their token (tenantId parameter ignored); staff and
    service callers pass tenantId (numeric id or tenant code)."""
    token = _bearer(request)
    payload = _decode(token, ctx)
    if payload.get("role") == "SERVICE":
        caller = Caller(role="SERVICE", user_id=None, name=str(payload.get("sub", "service")), claim_tenant_id=None,
                        tenant_id=None, tenant_code=None, token=token, is_service=True)
    else:
        try:
            user_id = int(payload["sub"])
        except (KeyError, ValueError, TypeError):
            raise Unauthorized("Invalid token")
        claim_tenant = payload.get("tenantId")
        caller = Caller(role=str(payload.get("role", "")), user_id=user_id, name=str(payload.get("name", "")),
                        claim_tenant_id=claim_tenant, tenant_id=claim_tenant, tenant_code=None, token=token)
    if caller.claim_tenant_id is None and tenant_param not in (None, ""):
        if str(tenant_param).lstrip("-").isdigit():
            caller.tenant_id = int(tenant_param)
        else:
            caller.tenant_id = await ctx.tenant_id_of(str(tenant_param))
            if caller.tenant_id is None:
                raise Validation("Unknown tenant", {"field": "tenantId"})
    if caller.tenant_id is None and require_tenant:
        if caller.is_service:
            raise Validation("tenantId is required", {"field": "tenantId"})
        raise Validation("tenantId is required for staff roles", {"field": "tenantId"})
    if caller.tenant_id is not None:
        caller.tenant_code = await ctx.tenant_code(caller.tenant_id)
    if caller.role == "RIDER":
        caller.rider_vehicle_codes = await rider_vehicles(ctx, caller)
    return caller


async def rider_vehicles(ctx: StubContext, caller: Caller) -> List[str]:
    now = utcnow()
    async with ctx.session_factory() as session:
        rows = (await session.execute(
            select(Vehicle.vehicle_code).join(VehicleRider, VehicleRider.vehicle_id == Vehicle.vehicle_id)
            .where(VehicleRider.user_id == caller.user_id, VehicleRider.valid_from <= now,
                   (VehicleRider.valid_to.is_(None)) | (VehicleRider.valid_to > now)))).scalars().all()
    return list(rows)


def forbid_roles(caller: Caller, *roles: str) -> None:
    if caller.role in roles:
        raise Forbidden()


async def user_names(session, user_ids: List[Optional[int]]) -> Dict[int, str]:
    ids = sorted({int(u) for u in user_ids if u is not None})
    if not ids:
        return {}
    rows = (await session.execute(select(AppUser.user_id, AppUser.name).where(AppUser.user_id.in_(ids)))).all()
    return {int(uid): name for uid, name in rows}


# -------------------------------------------------------------------------------------------
# Telemetry and devices
# -------------------------------------------------------------------------------------------
async def latest_samples(mongo: Any, tenant_code: Optional[str], vehicle_codes: Optional[List[str]] = None
                         ) -> Dict[str, Dict[str, Any]]:
    """Newest vehicle_telemetry document per car of one tenant (one aggregation)."""
    match: Dict[str, Any] = {}
    if tenant_code is not None:
        match["meta.tenantId"] = tenant_code
    if vehicle_codes is not None:
        match["meta.vehicleId"] = {"$in": list(vehicle_codes)}
    pipeline = [{"$match": match}, {"$sort": {"ts": -1}},
                {"$group": {"_id": "$meta.vehicleId", "latest": {"$first": "$$ROOT"}}}]
    out: Dict[str, Dict[str, Any]] = {}
    for doc in await mongo["vehicle_telemetry"].aggregate(pipeline).to_list(None):
        latest = doc["latest"]
        latest["ts"] = as_naive_utc(latest.get("ts"))
        out[doc["_id"]] = latest
    return out


def sample_position(sample: Optional[Dict[str, Any]]) -> tuple:
    """(lat, lon) of a telemetry document, or (None, None)."""
    if not sample:
        return None, None
    coords = (sample.get("location") or {}).get("coordinates") or [None, None]
    return coords[1], coords[0]


def telemetry_dto(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {"ts": iso(as_naive_utc(doc.get("ts"))), "speedKph": doc.get("speedKph"), "driveMode": doc.get("driveMode"),
            "ignition": doc.get("ignition"), "location": doc.get("location"), "network": doc.get("network"),
            "agent": doc.get("agent"), "devices": doc.get("devices")}


def device_ids(vehicle_code: str) -> Dict[str, str]:
    """{deviceId: type} for one car: dev-NN10 gateway, dev-NN11 interior mic, dev-NN12 exterior mic."""
    number = int(vehicle_code.split("-")[-1])
    return {f"dev-{number:02d}{suffix}": kind for suffix, kind in DEVICE_TYPES}


def devices_for(vehicle_code: str, vehicle_status: str, connection: str, last_seen: Optional[datetime],
                now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """A stable device list per car whose status follows the car's connection, plus the live
    FAULT overrides set by the demo generator."""
    now = now or utcnow()
    if vehicle_status == "RETIRED":
        base = "DISABLED"
    elif connection == "NEVER":
        base = "PROVISIONING"
    else:
        base = connection  # ONLINE / OFFLINE
    out = []
    for device_id, kind in device_ids(vehicle_code).items():
        status, reason = base, None
        override = DEVICE_OVERRIDES.get(device_id)
        if override is not None:
            if override.get("until") is not None and override["until"] < now:
                DEVICE_OVERRIDES.pop(device_id, None)
            elif base == "ONLINE":
                status, reason = override.get("status", status), override.get("faultReason")
        entry = {"deviceId": device_id, "type": kind, "status": status, "lastSeenAt": iso(last_seen),
                 "vehicleId": vehicle_code, "muted": False, "firmware": "edge-agent 0.4.1" if kind == "EDGE_GATEWAY" else None}
        if reason:
            entry["faultReason"] = reason
        out.append(entry)
    return out


def device_summary(devices: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"total": len(devices), "online": 0, "offline": 0, "fault": 0, "disabled": 0}
    for d in devices:
        key = d["status"].lower()
        if key in counts:
            counts[key] += 1
    return counts


def vehicle_dto(vehicle: Vehicle, sample: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or utcnow()
    ts = sample.get("ts") if sample else None
    connection = connection_of(ts, now)
    devices = devices_for(vehicle.vehicle_code, vehicle.status, connection, ts, now)
    lat, lon = sample_position(sample)
    return {"vehicleId": vehicle.vehicle_code, "vin": vehicle.vin, "label": vehicle.label, "make": vehicle.make,
            "model": vehicle.model, "year": vehicle.model_year, "status": vehicle.status, "connection": connection,
            "lastSeenAt": iso(ts), "retentionDays": vehicle.retention_days, "deviceSummary": device_summary(devices),
            "tenantId": vehicle.tenant_id, "iotThingName": vehicle.iot_thing_name,
            "position": {"lat": lat, "lon": lon} if lat is not None else None,
            "provisionedAt": iso(vehicle.provisioned_at), "createdAt": iso(vehicle.created_at),
            "updatedAt": iso(vehicle.updated_at)}


def env_flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")
