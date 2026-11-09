"""Vehicles, provisioning, network, retention, telemetry and the per-car clip list
(Sections 2.1, 2.3, 2.4 and 2.5 of the C2 design). The GET shapes are the stub's."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..auth import Caller, require_roles
from ..deps import any_caller, get_mongo, get_services, get_session_factory
from ..errors import BusinessRule, Validation
from ..models import Device, NetworkConfig, Vehicle
from ..services import registry
from ..services.clips import parse_window
from ..services.support import (devices_of, latest_samples, status_payload, telemetry_dto, vehicle_dto,
                                vehicle_in_scope)
from ..timeutil import iso, parse_iso

router = APIRouter(tags=["vehicles"])

ENGINEER = ("IOT_ENGINEER",)
DATA_WRITERS = ("OWNER", "FLEET_MANAGER", "IOT_ENGINEER")
DATA_READERS = ("OWNER", "FLEET_MANAGER", "IOT_ENGINEER", "SERVICE_STAFF", "SYS_ADMIN")


class VehicleBody(BaseModel):
    vehicleId: str
    vin: str
    make: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=40)
    year: int
    label: Optional[str] = Field(default=None, max_length=64)
    retentionDays: Optional[int] = None


class VehicleChanges(BaseModel):
    make: Optional[str] = None
    model: Optional[str] = None
    year: Optional[int] = None
    label: Optional[str] = None
    vin: Optional[str] = None
    vehicleId: Optional[str] = None


class ProvisionBody(BaseModel):
    rotate: bool = False


class Profile(BaseModel):
    priority: int
    ssid: str
    securityType: str
    password: Optional[str] = None


class NetworkBody(BaseModel):
    profiles: List[Profile]


class RetentionBody(BaseModel):
    retentionDays: int


def _page(page: Optional[int], size: Optional[int]) -> tuple:
    page, size = page or 1, size or 25
    if page < 1 or not 1 <= size <= 100:
        raise Validation("page must be 1 or more and size 1 to 100", {"field": "size", "max": 100})
    return page, size


@router.get("/vehicles")
async def list_vehicles(status: Optional[str] = None, connection: Optional[str] = None, q: Optional[str] = None,
                        page: Optional[int] = None, size: Optional[int] = None, caller: Caller = Depends(any_caller),
                        session_factory: Any = Depends(get_session_factory), mongo: Any = Depends(get_mongo)):
    """{items[{vehicleId, vin, label, make, model, year, status, connection, lastSeenAt, retentionDays,
    deviceSummary{total, online, offline, fault, disabled}, ...}], page, size, total}"""
    if caller.tenant_id is None:
        raise Validation("tenantId is required", {"field": "tenantId"})
    page, size = _page(page, size)
    async with session_factory() as session:
        stmt = select(Vehicle).where(Vehicle.tenant_id == caller.tenant_id).order_by(Vehicle.vehicle_code)
        if (status or "").upper() != "RETIRED":
            stmt = stmt.where(Vehicle.status != "RETIRED")
        vehicles = list((await session.execute(stmt)).scalars())
        if caller.rider_vehicle_codes is not None:
            vehicles = [v for v in vehicles if v.vehicle_code in caller.rider_vehicle_codes]
        devices = await devices_of(session, [v.vehicle_id for v in vehicles])
    samples = await latest_samples(mongo, caller.tenant_code)
    items = [vehicle_dto(v, devices.get(v.vehicle_id, []), samples.get(v.vehicle_code)) for v in vehicles]
    if status:
        items = [i for i in items if i["status"] == status.upper()]
    if connection:
        items = [i for i in items if i["connection"] == connection.upper()]
    if q:
        needle = q.lower()
        items = [i for i in items if needle in i["vehicleId"].lower() or needle in (i["label"] or "").lower()
                 or needle in (i["vin"] or "").lower()]
    total = len(items)
    start = (page - 1) * size
    return {"items": items[start:start + size], "page": page, "size": size, "total": total}


@router.post("/vehicles", status_code=201)
async def create_vehicle(body: VehicleBody, caller: Caller = Depends(any_caller),
                         session_factory: Any = Depends(get_session_factory), services: Any = Depends(get_services)):
    require_roles(caller, *ENGINEER)
    if caller.tenant_id is None:
        raise Validation("tenantId is required", {"field": "tenantId"})
    async with session_factory() as session:
        vehicle = await registry.create_vehicle(session, caller, caller.tenant_id, body.model_dump())
        await session.commit()
    ok = await services.c1.subscription_defaults(vehicle.vehicle_code, vehicle.tenant_id)
    out = vehicle_dto(vehicle, [])
    out["subscriptionsCreated"] = ok
    return out


@router.get("/vehicles/{vehicle_code}")
async def get_vehicle(vehicle_code: str, caller: Caller = Depends(any_caller),
                      session_factory: Any = Depends(get_session_factory), mongo: Any = Depends(get_mongo)):
    async with session_factory() as session:
        vehicle = await vehicle_in_scope(session, caller, vehicle_code)
        devices = (await devices_of(session, [vehicle.vehicle_id]))[vehicle.vehicle_id]
        nets = list((await session.execute(select(NetworkConfig).where(
            NetworkConfig.vehicle_id == vehicle.vehicle_id))).scalars())
    code = caller.tenant_code or await _tenant_code(session_factory, vehicle)
    sample = (await latest_samples(mongo, code, [vehicle_code])).get(vehicle_code)
    from ..services.support import device_dto
    out = vehicle_dto(vehicle, devices, sample)
    out["devices"] = [device_dto(d, vehicle.vehicle_code) for d in devices]
    out["provisioning"] = {"mqttUsername": vehicle.iot_thing_name, "credentialVersion": vehicle.credential_version,
                           "provisionedAt": iso(vehicle.provisioned_at)}
    out["network"] = {"profiles": registry.network_rows_dto(nets)}
    return out


async def _tenant_code(session_factory: Any, vehicle: Vehicle) -> Optional[str]:
    from ..models import Tenant
    async with session_factory() as session:
        t = await session.get(Tenant, vehicle.tenant_id)
    return t.code if t else None


@router.put("/vehicles/{vehicle_code}")
async def update_vehicle(vehicle_code: str, body: VehicleChanges, caller: Caller = Depends(any_caller),
                         session_factory: Any = Depends(get_session_factory)):
    require_roles(caller, *ENGINEER)
    async with session_factory() as session:
        vehicle = await vehicle_in_scope(session, caller, vehicle_code)
        registry.update_vehicle(vehicle, body.model_dump(exclude_unset=True))
        devices = (await devices_of(session, [vehicle.vehicle_id]))[vehicle.vehicle_id]
        await session.commit()
    return vehicle_dto(vehicle, devices)


@router.delete("/vehicles/{vehicle_code}")
async def retire_vehicle(vehicle_code: str, confirm: bool = Query(default=False), caller: Caller = Depends(any_caller),
                         session_factory: Any = Depends(get_session_factory), services: Any = Depends(get_services)):
    require_roles(caller, *ENGINEER)
    if not confirm:
        raise Validation("Retiring is final: send confirm=true", {"field": "confirm"})
    async with session_factory() as session:
        vehicle = await vehicle_in_scope(session, caller, vehicle_code)
        changed = await registry.retire_vehicle(session, caller, vehicle, services.commands, services.broker)
        await session.commit()
    for device, old in changed:
        await services.tracker.admin_change(device, vehicle.vehicle_code, vehicle.tenant_id, old)
    return {"vehicleId": vehicle.vehicle_code, "status": vehicle.status}


@router.get("/vehicles/{vehicle_code}/telemetry")
async def telemetry(vehicle_code: str, limit: int = Query(default=60, ge=1, le=1000),
                    from_: Optional[str] = Query(default=None, alias="from"), to: Optional[str] = None,
                    caller: Caller = Depends(any_caller), session_factory: Any = Depends(get_session_factory),
                    mongo: Any = Depends(get_mongo)):
    """Newest first: [{ts, speedKph, driveMode, ignition, location{type, coordinates:[lon, lat]}, network, ...}]"""
    async with session_factory() as session:
        vehicle = await vehicle_in_scope(session, caller, vehicle_code)
    code = caller.tenant_code or await _tenant_code(session_factory, vehicle)
    match: Dict[str, Any] = {"meta.tenantId": code, "meta.vehicleId": vehicle_code}
    window: Dict[str, Any] = {}
    if from_:
        window["$gte"] = parse_iso(from_, "from")
    if to:
        window["$lt"] = parse_iso(to, "to")
    if window:
        match["ts"] = window
    docs = await mongo["vehicle_telemetry"].find(match).sort("ts", -1).limit(limit).to_list(limit)
    return [telemetry_dto(d) for d in docs]


# -- provisioning ----------------------------------------------------------------------------------------
@router.post("/vehicles/{vehicle_code}/provision", status_code=201)
async def provision(vehicle_code: str, body: ProvisionBody = Body(default=ProvisionBody()),
                    caller: Caller = Depends(any_caller), session_factory: Any = Depends(get_session_factory),
                    services: Any = Depends(get_services)):
    require_roles(caller, *ENGINEER)
    async with session_factory() as session:
        vehicle = await vehicle_in_scope(session, caller, vehicle_code)
        code = caller.tenant_code or await services.tenants.code_of(vehicle.tenant_id)
        result = await registry.provision(session, caller, vehicle, body.rotate, services.settings, services.broker, code)
        await session.commit()
    return result


@router.delete("/vehicles/{vehicle_code}/provision")
async def revoke(vehicle_code: str, caller: Caller = Depends(any_caller),
                 session_factory: Any = Depends(get_session_factory), services: Any = Depends(get_services)):
    require_roles(caller, *ENGINEER)
    async with session_factory() as session:
        vehicle = await vehicle_in_scope(session, caller, vehicle_code)
        await registry.revoke(session, caller, vehicle, services.broker)
        await session.commit()
    return {"vehicleId": vehicle.vehicle_code, "status": vehicle.status}


# -- network ---------------------------------------------------------------------------------------------
@router.get("/vehicles/{vehicle_code}/network")
async def get_network(vehicle_code: str, caller: Caller = Depends(any_caller),
                      session_factory: Any = Depends(get_session_factory), mongo: Any = Depends(get_mongo)):
    require_roles(caller, *DATA_READERS[2:])
    from ..models import DeviceCommand
    async with session_factory() as session:
        vehicle = await vehicle_in_scope(session, caller, vehicle_code)
        rows = list((await session.execute(select(NetworkConfig).where(
            NetworkConfig.vehicle_id == vehicle.vehicle_id))).scalars())
        last = (await session.execute(select(DeviceCommand).where(
            DeviceCommand.vehicle_id == vehicle.vehicle_id, DeviceCommand.command == "SET_NETWORK")
            .order_by(DeviceCommand.command_id.desc()).limit(1))).scalars().first()
    code = caller.tenant_code or await _tenant_code(session_factory, vehicle)
    sample = (await latest_samples(mongo, code, [vehicle_code])).get(vehicle_code)
    return {"vehicleId": vehicle.vehicle_code, "profiles": registry.network_rows_dto(rows), "cellularApn": None,
            "lastApplied": {"commandId": last.command_id, "status": last.status,
                            "at": iso(last.acked_at or last.sent_at or last.created_at)} if last else None,
            "current": (sample or {}).get("network")}


@router.put("/vehicles/{vehicle_code}/network", status_code=202)
async def put_network(vehicle_code: str, body: NetworkBody, caller: Caller = Depends(any_caller),
                      session_factory: Any = Depends(get_session_factory), services: Any = Depends(get_services)):
    require_roles(caller, *ENGINEER)
    async with session_factory() as session:
        vehicle = await vehicle_in_scope(session, caller, vehicle_code)
        if vehicle.status == "RETIRED":
            raise BusinessRule("BR-C2-11", "The car is retired", field="vehicleId")
        profiles = await registry.put_network(session, caller, vehicle, [p.model_dump() for p in body.profiles],
                                              services.box)
        cmd = await services.commands.network(session, caller, vehicle, profiles)
        await session.commit()
        command_id = cmd.command_id
    state = await services.commands.deliver(command_id)
    return {"commandId": command_id, "state": state, "profiles": profiles}


# -- per-car data ----------------------------------------------------------------------------------------
@router.get("/vehicles/{vehicle_code}/clips")
async def list_clips(vehicle_code: str, from_: Optional[str] = Query(default=None, alias="from"),
                     to: Optional[str] = None, deviceId: Optional[str] = None, cls: Optional[str] = Query(default=None,
                                                                                                      alias="class"),
                     alertOnly: bool = False, page: Optional[int] = None, size: Optional[int] = None,
                     caller: Caller = Depends(any_caller), session_factory: Any = Depends(get_session_factory),
                     services: Any = Depends(get_services)):
    require_roles(caller, *DATA_READERS)
    page, size = _page(page, size)
    start, end = parse_window(from_, to)
    async with session_factory() as session:
        vehicle = await vehicle_in_scope(session, caller, vehicle_code)
        code = caller.tenant_code or await services.tenants.code_of(vehicle.tenant_id)
        return await services.clips.list_for_vehicle(session, vehicle, code, start, end, deviceId, cls, alertOnly,
                                                     page, size)


@router.put("/vehicles/{vehicle_code}/retention")
async def set_retention(vehicle_code: str, body: RetentionBody, caller: Caller = Depends(any_caller),
                        session_factory: Any = Depends(get_session_factory)):
    """BR-C2-23: applies to clips captured from now on."""
    require_roles(caller, *DATA_WRITERS)
    if not 7 <= body.retentionDays <= 365:
        raise BusinessRule("BR-C2-23", "retentionDays must be 7 to 365", field="retentionDays")
    from ..timeutil import utcnow
    async with session_factory() as session:
        vehicle = await vehicle_in_scope(session, caller, vehicle_code)
        vehicle.retention_days = body.retentionDays
        vehicle.updated_at = utcnow()
        await session.commit()
    return {"vehicleId": vehicle.vehicle_code, "retentionDays": vehicle.retention_days, "appliesFrom": iso(utcnow())}
