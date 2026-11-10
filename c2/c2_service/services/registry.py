"""M1 vehicles, M2 devices, M3 provisioning and M8 network settings (Sections 4.1 to 4.3 and 4.8
of the C2 design). The routers call these inside one session; events go out after the commit.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from ..auth import Caller, issue_device_token
from ..errors import BusinessRule, Conflict, NotFound, Validation
from ..models import DEVICE_TYPES, MIC_TYPES, Device, NetworkConfig, Tenant, Vehicle, VehicleRider
from ..timeutil import utcnow
from .commands import DEFAULT_MIC_CONFIG
from .support import audit

log = logging.getLogger("scicp.c2.registry")

CODE_RE = re.compile(r"^[A-Z]{2}-[0-9]{2,4}$")
VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
TYPE_LIMITS = {"EDGE_GATEWAY": 1, "MIC": 4, "CAMERA": 4}
SECURITY_TYPES = ("OPEN", "WPA2_PSK", "WPA3_SAE")
DEVICE_SUFFIX = {"EDGE_GATEWAY": "10", "MIC_INTERIOR": "11", "MIC_EXTERIOR": "12"}


# -- M1 vehicles ----------------------------------------------------------------------------------------
async def check_vehicle_limit(session, tenant_id: int) -> Tenant:
    """BR-C2-02: cars not RETIRED < tenant.max_vehicles."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise Validation("Unknown tenant", {"field": "tenantId"})
    count = (await session.execute(select(func.count()).select_from(Vehicle).where(
        Vehicle.tenant_id == tenant_id, Vehicle.status != "RETIRED"))).scalar_one()
    if count >= tenant.max_vehicles:
        raise BusinessRule("BR-C2-02", f"The {tenant.plan} plan allows {tenant.max_vehicles} vehicles",
                           field="vehicleId", limit=tenant.max_vehicles)
    return tenant


def check_identity(vehicle_code: str, vin: str, year: int) -> None:
    """BR-C2-03."""
    if not CODE_RE.match(vehicle_code or ""):
        raise BusinessRule("BR-C2-03", "vehicleId must look like AV-01 (two letters, a dash, 2 to 4 digits)",
                           field="vehicleId")
    if not VIN_RE.match((vin or "").upper()):
        raise BusinessRule("BR-C2-03", "The VIN has 17 characters without I, O or Q", field="vin")
    if not 1990 <= int(year) <= utcnow().year + 1:
        raise BusinessRule("BR-C2-03", f"year must be 1990 to {utcnow().year + 1}", field="year")


async def create_vehicle(session, caller: Caller, tenant_id: int, body: Dict[str, Any]) -> Vehicle:
    code, vin = str(body.get("vehicleId", "")).upper(), str(body.get("vin", "")).upper()
    check_identity(code, vin, body.get("year") or 0)
    retention = int(body.get("retentionDays") or 90)
    if not 7 <= retention <= 365:
        raise BusinessRule("BR-C2-23", "retentionDays must be 7 to 365", field="retentionDays")
    if (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalars().first() is not None:
        raise Conflict("A vehicle with this VIN exists", {"field": "vin"})
    if (await session.execute(select(Vehicle).where(Vehicle.tenant_id == tenant_id,
                                                    Vehicle.vehicle_code == code))).scalars().first() is not None:
        raise Conflict("This vehicleId is used in the tenant (codes are never reused)", {"field": "vehicleId"})
    await check_vehicle_limit(session, tenant_id)
    now = utcnow()
    vehicle = Vehicle(tenant_id=tenant_id, vehicle_code=code, vin=vin, make=str(body.get("make", ""))[:40],
                      model=str(body.get("model", ""))[:40], model_year=int(body["year"]),
                      label=(body.get("label") or None), retention_days=retention, credential_version=0,
                      status="REGISTERED", created_at=now, updated_at=now)
    session.add(vehicle)
    await session.flush()
    audit(session, caller, tenant_id, "VEHICLE_CREATE", "vehicle", code, None, {"vin": vin})
    return vehicle


def update_vehicle(vehicle: Vehicle, body: Dict[str, Any]) -> None:
    if vehicle.status == "RETIRED":
        raise Conflict("The vehicle is retired")
    for fixed in ("vin", "vehicleId"):
        if fixed in body and body[fixed] is not None:
            raise BusinessRule("BR-C2-03", f"{fixed} cannot change after creation", field=fixed)
    if "year" in body and body["year"] is not None:
        check_identity(vehicle.vehicle_code, vehicle.vin, body["year"])
        vehicle.model_year = int(body["year"])
    for key, col in (("make", "make"), ("model", "model"), ("label", "label")):
        if key in body and body[key] is not None:
            setattr(vehicle, col, str(body[key])[:64 if key == "label" else 40])
    vehicle.updated_at = utcnow()


async def retire_vehicle(session, caller: Caller, vehicle: Vehicle, commands: Any, broker: Any) -> List[tuple]:
    """BR-C2-04; returns the (device, old status) pairs for the events."""
    if vehicle.status == "RETIRED":
        raise Conflict("The vehicle is already retired")
    if vehicle.iot_thing_name and vehicle.provisioned_at is not None:
        await broker.delete_car(vehicle.iot_thing_name)
    vehicle.credential_version = (vehicle.credential_version or 0) + 1
    vehicle.status, vehicle.retired_at, vehicle.updated_at = "RETIRED", utcnow(), utcnow()
    changed = []
    for d in (await session.execute(select(Device).where(Device.vehicle_id == vehicle.vehicle_id,
                                                         Device.deleted_at.is_(None)))).scalars():
        if d.status != "DISABLED":
            changed.append((d, d.status))
            d.status = "DISABLED"
    await commands.supersede_all(session, vehicle.vehicle_id)
    now = utcnow()
    for ride in (await session.execute(select(VehicleRider).where(VehicleRider.vehicle_id == vehicle.vehicle_id,
                                                                  VehicleRider.valid_to.is_(None)))).scalars():
        ride.valid_to = now
    audit(session, caller, vehicle.tenant_id, "RETIRE_VEHICLE", "vehicle", vehicle.vehicle_code)
    return changed


# -- M2 devices ----------------------------------------------------------------------------------------
def _type_group(device_type: str) -> str:
    return "MIC" if device_type in MIC_TYPES else device_type


async def next_device_code(session, vehicle_code: str, device_type: str) -> str:
    """dev-NNSS like the stub and the seed: NN the car number, SS 10 gateway, 11 interior, 12 exterior;
    further devices take the next free number."""
    number = int(vehicle_code.split("-")[-1])
    taken = set((await session.execute(select(Device.device_code))).scalars())
    preferred = DEVICE_SUFFIX.get(device_type)
    if preferred and f"dev-{number:02d}{preferred}" not in taken:
        return f"dev-{number:02d}{preferred}"
    for suffix in range(13, 100):
        code = f"dev-{number:02d}{suffix:02d}"
        if code not in taken:
            return code
    raise Conflict("No free device code for this car")


async def add_device(session, caller: Caller, vehicle: Vehicle, body: Dict[str, Any],
                     serial: Optional[str] = None) -> Device:
    """BR-C2-05."""
    device_type = str(body.get("type", "")).upper()
    if device_type not in DEVICE_TYPES:
        raise Validation(f"type must be one of {', '.join(DEVICE_TYPES)}", {"field": "type"})
    if vehicle.status == "RETIRED":
        raise BusinessRule("BR-C2-05", "No device can be added to a retired car", field="vehicleId")
    current = (await session.execute(select(Device.device_type).where(Device.vehicle_id == vehicle.vehicle_id,
                                                                      Device.deleted_at.is_(None)))).scalars().all()
    group = _type_group(device_type)
    if sum(1 for t in current if _type_group(t) == group) >= TYPE_LIMITS[group]:
        raise BusinessRule("BR-C2-05", f"A car has at most {TYPE_LIMITS[group]} {group.lower().replace('_', ' ')}"
                           f"{'s' if TYPE_LIMITS[group] > 1 else ''}", field="type")
    serial = serial or str(body.get("serialNo") or "").strip()
    if not serial:
        raise Validation("serialNo is required", {"field": "serialNo"})
    if (await session.execute(select(Device).where(Device.serial_no == serial))).scalars().first() is not None:
        raise Conflict("A device with this serial number exists", {"field": "serialNo"})
    code = await next_device_code(session, vehicle.vehicle_code, device_type)
    device = Device(tenant_id=vehicle.tenant_id, vehicle_id=vehicle.vehicle_id, device_code=code,
                    device_type=device_type, position=str(body.get("position") or default_position(device_type))[:32],
                    serial_no=serial[:64], firmware_version=(body.get("firmwareVersion") or None), status="PROVISIONING",
                    muted=False, config_json=dict(DEFAULT_MIC_CONFIG) if device_type in MIC_TYPES else None,
                    config_version=1, config_state="CONFIRMED" if device_type in MIC_TYPES else "NONE",
                    created_at=utcnow())
    session.add(device)
    await session.flush()
    return device


def default_position(device_type: str) -> str:
    return {"EDGE_GATEWAY": "trunk", "MIC_INTERIOR": "cabin roof", "MIC_EXTERIOR": "front bumper",
            "CAMERA": "windshield"}.get(device_type, "unknown")


async def remove_device(session, device: Device, vehicle: Vehicle, commands: Any) -> None:
    """BR-C2-06: soft delete; not the gateway of a car that is not retired."""
    if device.device_type == "EDGE_GATEWAY" and vehicle.status != "RETIRED":
        raise BusinessRule("BR-C2-06", "The gateway of an active car cannot be deleted; edit its serial number",
                           field="deviceId")
    device.deleted_at = utcnow()
    await commands.supersede_all(session, vehicle.vehicle_id, device.device_id)


# -- M3 provisioning -------------------------------------------------------------------------------------
class NoBrokerAdmin:
    """C2_BROKER_AUTH=none: the course stack's Mosquitto allows anonymous clients inside the compose
    network (deploy/mosquitto.conf), so logins are issued and recorded but not enforced by the broker.
    The dynamic-security client of Section 2.8 replaces this on a public broker."""
    mode = "none"

    async def create_car(self, username: str, password: str, topic_root: str) -> None:
        log.info("broker auth off: login %s issued for %s (not enforced by the broker)", username, topic_root)

    async def set_password(self, username: str, password: str) -> None:
        log.info("broker auth off: password of %s rotated", username)

    async def delete_car(self, username: str) -> None:
        log.info("broker auth off: login %s revoked", username)


def agent_yaml(settings: Any, tenant_code: str, vehicle: Vehicle, username: str, password: str, token: str,
               devices: List[Device]) -> str:
    mics = [d for d in devices if d.device_type in MIC_TYPES]
    lines = [
        "# SCICP edge agent configuration - written by C2 provisioning; keep it secret",
        f"tenantId: {tenant_code}",
        f"vehicleId: {vehicle.vehicle_code}",
        "mqtt:",
        f"  host: {settings.c2_public_mqtt_host}",
        f"  port: {settings.c2_public_mqtt_port}",
        f"  username: {username}",
        f"  password: {password}",
        f"  clientId: {username}",
        f"  topicRoot: scicp/v1/{tenant_code}/{vehicle.vehicle_code}",
        "api:",
        f"  baseUrl: {settings.c2_public_api_base}",
        f"  ingestToken: {token}",
        "devices:",
    ]
    for d in devices:
        lines.append(f"  - {{deviceId: {d.device_code}, type: {d.device_type}}}")
    first_mic = next((d for d in mics if d.device_type == "MIC_EXTERIOR"), mics[0] if mics else None)
    cfg = (first_mic.config_json if first_mic else None) or DEFAULT_MIC_CONFIG
    lines += [f"micDevice: {first_mic.device_code if first_mic else ''}",
              f"micGainDb: {cfg.get('micGainDb', 6)}",
              f"detectionThreshold: {cfg.get('detectionThreshold', 0.40)}"]
    return "\n".join(lines) + "\n"


async def provision(session, caller: Caller, vehicle: Vehicle, rotate: bool, settings: Any, broker: Any,
                    tenant_code: str) -> Dict[str, Any]:
    """BR-C2-07 to BR-C2-09: one login per car, a device token, the agent.yaml - shown once."""
    if vehicle.status == "RETIRED":
        raise BusinessRule("BR-C2-07", "A retired car cannot be provisioned", field="vehicleId")
    already = vehicle.status in ("PROVISIONED", "ACTIVE") or vehicle.provisioned_at is not None
    if already and not rotate:
        raise Conflict("The car is already provisioned; send rotate: true to issue new credentials")
    devices = list((await session.execute(select(Device).where(Device.vehicle_id == vehicle.vehicle_id,
                                                               Device.deleted_at.is_(None))
                                          .order_by(Device.device_code))).scalars())
    if not any(d.device_type == "EDGE_GATEWAY" for d in devices):
        gw = await add_device(session, caller, vehicle, {"type": "EDGE_GATEWAY", "position": "trunk"},
                              serial=f"GW-{tenant_code}-{vehicle.vehicle_code}-{secrets.token_hex(3).upper()}")
        devices.append(gw)
    username = f"{tenant_code}-{vehicle.vehicle_code}"
    password = secrets.token_urlsafe(18)
    topic_root = f"scicp/v1/{tenant_code}/{vehicle.vehicle_code}"
    if already and vehicle.iot_thing_name:
        await broker.set_password(username, password)
    else:
        await broker.create_car(username, password, topic_root)
    vehicle.iot_thing_name = username
    vehicle.credential_version = (vehicle.credential_version or 0) + 1
    if vehicle.status == "REGISTERED":
        vehicle.status = "PROVISIONED"
    vehicle.provisioned_at = utcnow()
    vehicle.updated_at = utcnow()
    token = issue_device_token(settings, tenant_code, vehicle.tenant_id, vehicle.vehicle_code,
                               vehicle.credential_version)
    audit(session, caller, vehicle.tenant_id, "PROVISION", "vehicle", vehicle.vehicle_code, None,
          {"rotate": rotate, "credentialVersion": vehicle.credential_version})
    devices.sort(key=lambda d: d.device_code)
    return {"vehicleId": vehicle.vehicle_code, "status": vehicle.status, "brokerAuth": broker.mode,
            "credentialVersion": vehicle.credential_version,
            "mqtt": {"host": settings.c2_public_mqtt_host, "port": settings.c2_public_mqtt_port, "username": username,
                     "password": password, "clientId": username, "topicRoot": topic_root},
            "api": {"baseUrl": settings.c2_public_api_base, "ingestToken": token},
            "devices": [d.device_code for d in devices],
            "agentYaml": agent_yaml(settings, tenant_code, vehicle, username, password, token, devices)}


async def revoke(session, caller: Caller, vehicle: Vehicle, broker: Any) -> None:
    if vehicle.provisioned_at is None or vehicle.status not in ("PROVISIONED", "ACTIVE"):
        raise Conflict("The car is not provisioned")
    if vehicle.iot_thing_name:
        await broker.delete_car(vehicle.iot_thing_name)
    vehicle.credential_version = (vehicle.credential_version or 0) + 1
    vehicle.status, vehicle.provisioned_at, vehicle.updated_at = "REGISTERED", None, utcnow()
    audit(session, caller, vehicle.tenant_id, "REVOKE", "vehicle", vehicle.vehicle_code)


# -- M8 network settings ---------------------------------------------------------------------------------
class SecretBox:
    """Fernet (AES-128-CBC + HMAC) for the Wi-Fi passwords, keyed from NETWORK_SECRET or the service secret."""

    def __init__(self, material: str) -> None:
        from cryptography.fernet import Fernet
        key = base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest())
        self._fernet = Fernet(key)

    def seal(self, text: str) -> bytes:
        return self._fernet.encrypt(text.encode())

    def open(self, blob: bytes) -> str:
        return self._fernet.decrypt(blob).decode()


def check_profiles(profiles: List[Dict[str, Any]], stored: Dict[str, NetworkConfig]) -> None:
    """BR-C2-25."""
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= 3:
        raise BusinessRule("BR-C2-25", "Give one to three Wi-Fi profiles", field="profiles")
    priorities = [p.get("priority") for p in profiles]
    if sorted(priorities) != sorted(set(priorities)) or any(p not in (1, 2, 3) for p in priorities):
        raise BusinessRule("BR-C2-25", "Priorities must be distinct, 1 to 3", field="priority")
    for p in profiles:
        ssid = str(p.get("ssid") or "")
        if not 1 <= len(ssid.encode()) <= 32:
            raise BusinessRule("BR-C2-25", "SSID must be 1 to 32 bytes", field="ssid")
        sec = str(p.get("securityType") or "")
        if sec not in SECURITY_TYPES:
            raise BusinessRule("BR-C2-25", "securityType must be OPEN, WPA2_PSK or WPA3_SAE", field="securityType")
        password = p.get("password")
        if sec == "OPEN":
            continue
        if password in (None, ""):
            old = stored.get(ssid)
            if old is None or old.password_enc is None:
                raise BusinessRule("BR-C2-25", f"{sec} needs a password", field="password")
        elif not 8 <= len(str(password)) <= 63:
            raise BusinessRule("BR-C2-25", "The password must have 8 to 63 characters", field="password")


async def put_network(session, caller: Caller, vehicle: Vehicle, profiles: List[Dict[str, Any]], box: SecretBox
                      ) -> List[dict]:
    rows = list((await session.execute(select(NetworkConfig).where(NetworkConfig.vehicle_id == vehicle.vehicle_id)))
                .scalars())
    stored = {r.ssid: r for r in rows}
    check_profiles(profiles, stored)
    keep = {}
    for p in profiles:
        sec, ssid = p["securityType"], p["ssid"]
        password = p.get("password")
        if sec == "OPEN":
            blob = None
        elif password:
            blob = box.seal(str(password))
        else:
            blob = stored[ssid].password_enc
        keep[ssid] = (int(p["priority"]), sec, blob)
    for r in rows:
        await session.delete(r)
    await session.flush()
    for ssid, (priority, sec, blob) in keep.items():
        session.add(NetworkConfig(tenant_id=vehicle.tenant_id, vehicle_id=vehicle.vehicle_id, priority=priority,
                                  ssid=ssid, security_type=sec, password_enc=blob, updated_by=caller.user_id,
                                  updated_at=utcnow()))
    await session.flush()
    return [{"priority": pr, "ssid": s, "securityType": sec} for s, (pr, sec, _) in sorted(keep.items(), key=lambda kv: kv[1][0])]


def network_rows_dto(rows: List[NetworkConfig]) -> List[dict]:
    return [{"priority": r.priority, "ssid": r.ssid, "securityType": r.security_type,
             "passwordSet": r.password_enc is not None} for r in sorted(rows, key=lambda r: r.priority)]
