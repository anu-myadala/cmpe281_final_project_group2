"""Devices, settings and commands (Sections 2.2 and 2.3 of the C2 design)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select

from ..auth import Caller, require_roles
from ..deps import any_caller, get_services, get_session_factory
from ..errors import NotFound
from ..models import Device, DeviceCommand, Vehicle
from ..services import registry
from ..services.commands import command_dto
from ..services.support import device_dto, device_in_scope, devices_of, vehicle_in_scope

router = APIRouter(tags=["devices"])
ENGINEER = ("IOT_ENGINEER",)
STAFF_READ = ("IOT_ENGINEER", "SERVICE_STAFF", "SYS_ADMIN")


class DeviceBody(BaseModel):
    type: str
    position: Optional[str] = None
    serialNo: str
    firmwareVersion: Optional[str] = None


class DeviceChanges(BaseModel):
    position: Optional[str] = None
    serialNo: Optional[str] = None
    firmwareVersion: Optional[str] = None
    enabled: Optional[bool] = None


class ConfigBody(BaseModel):
    micGainDb: Optional[float] = None
    detectionThreshold: Optional[float] = None
    sampleRateHz: Optional[int] = None
    clipSeconds: Optional[int] = None


class CommandBody(BaseModel):
    command: str
    params: Optional[Dict[str, Any]] = None


@router.get("/vehicles/{vehicle_code}/devices")
async def list_devices(vehicle_code: str, includeDeleted: bool = False, caller: Caller = Depends(any_caller),
                       session_factory: Any = Depends(get_session_factory)):
    """[{deviceId, type, status, lastSeenAt, vehicleId, muted, firmware, ...}] (the stub's list, plus config)."""
    async with session_factory() as session:
        vehicle = await vehicle_in_scope(session, caller, vehicle_code)
        devices = (await devices_of(session, [vehicle.vehicle_id], include_deleted=includeDeleted))[vehicle.vehicle_id]
    return [device_dto(d, vehicle.vehicle_code) for d in devices]


@router.get("/devices")
async def list_all_devices(caller: Caller = Depends(any_caller), session_factory: Any = Depends(get_session_factory)):
    """Flat device list of the tenant (all tenants for staff without tenantId), as the stub returns it."""
    async with session_factory() as session:
        stmt = select(Vehicle).order_by(Vehicle.vehicle_code)
        if caller.tenant_id is not None:
            stmt = stmt.where(Vehicle.tenant_id == caller.tenant_id)
        vehicles = list((await session.execute(stmt)).scalars())
        if caller.rider_vehicle_codes is not None:
            vehicles = [v for v in vehicles if v.vehicle_code in caller.rider_vehicle_codes]
        devices = await devices_of(session, [v.vehicle_id for v in vehicles])
    items: List[dict] = []
    for v in vehicles:
        items.extend(device_dto(d, v.vehicle_code) for d in devices.get(v.vehicle_id, []))
    return {"items": items, "total": len(items)}


@router.post("/vehicles/{vehicle_code}/devices", status_code=201)
async def add_device(vehicle_code: str, body: DeviceBody, caller: Caller = Depends(any_caller),
                     session_factory: Any = Depends(get_session_factory)):
    require_roles(caller, *ENGINEER)
    async with session_factory() as session:
        vehicle = await vehicle_in_scope(session, caller, vehicle_code)
        device = await registry.add_device(session, caller, vehicle, body.model_dump())
        await session.commit()
    return device_dto(device, vehicle.vehicle_code)


async def _device_and_vehicle(session, caller: Caller, device_code: str) -> tuple:
    device = await device_in_scope(session, caller, device_code)
    vehicle = await session.get(Vehicle, device.vehicle_id)
    if vehicle is None:
        raise NotFound("Device not found")
    return device, vehicle


@router.get("/devices/{device_code}")
async def get_device(device_code: str, caller: Caller = Depends(any_caller),
                     session_factory: Any = Depends(get_session_factory)):
    async with session_factory() as session:
        device, vehicle = await _device_and_vehicle(session, caller, device_code)
    return device_dto(device, vehicle.vehicle_code)


@router.put("/devices/{device_code}")
async def update_device(device_code: str, body: DeviceChanges, caller: Caller = Depends(any_caller),
                        session_factory: Any = Depends(get_session_factory), services: Any = Depends(get_services)):
    require_roles(caller, *ENGINEER)
    changes = body.model_dump(exclude_unset=True)
    command_id = None
    async with session_factory() as session:
        device, vehicle = await _device_and_vehicle(session, caller, device_code)
        from ..errors import Conflict
        if vehicle.status == "RETIRED":
            raise Conflict("The vehicle is retired")
        old = device.status
        if changes.get("position"):
            device.position = changes["position"][:32]
        if changes.get("firmwareVersion"):
            device.firmware_version = changes["firmwareVersion"][:32]
        if changes.get("serialNo") and changes["serialNo"] != device.serial_no:
            taken = (await session.execute(select(Device).where(Device.serial_no == changes["serialNo"]))).scalars().first()
            if taken is not None:
                raise Conflict("A device with this serial number exists", {"field": "serialNo"})
            device.serial_no = changes["serialNo"][:64]
        if "enabled" in changes and changes["enabled"] is not None:      # BR-C2-10
            if changes["enabled"] is False and device.status != "DISABLED":
                device.status = "DISABLED"
                await services.commands.supersede_all(session, vehicle.vehicle_id, device.device_id)
                if device.device_type in ("MIC_INTERIOR", "MIC_EXTERIOR") and vehicle.status in ("PROVISIONED", "ACTIVE"):
                    from ..models import DeviceCommand as DC
                    from ..timeutil import utcnow
                    cmd = DC(tenant_id=vehicle.tenant_id, vehicle_id=vehicle.vehicle_id, device_id=device.device_id,
                             issued_by=caller.user_id, command="MUTE", status="PENDING", params_json={"reason": "DISABLED"},
                             created_at=utcnow())
                    session.add(cmd)
                    await session.flush()
                    command_id = cmd.command_id
            elif changes["enabled"] is True and device.status == "DISABLED":
                device.status = "PROVISIONING"
                device.last_seen_at = None
                if device.device_type in ("MIC_INTERIOR", "MIC_EXTERIOR") and vehicle.status in ("PROVISIONED", "ACTIVE"):
                    cmd = await services.commands.simple(session, caller, device, vehicle, "UNMUTE")
                    command_id = cmd.command_id
        await session.commit()
    if command_id is not None:
        await services.commands.deliver(command_id)
    await services.tracker.admin_change(device, vehicle.vehicle_code, vehicle.tenant_id, old)
    return device_dto(device, vehicle.vehicle_code)


@router.delete("/devices/{device_code}", status_code=204)
async def delete_device(device_code: str, caller: Caller = Depends(any_caller),
                       session_factory: Any = Depends(get_session_factory), services: Any = Depends(get_services)):
    require_roles(caller, *ENGINEER)
    async with session_factory() as session:
        device, vehicle = await _device_and_vehicle(session, caller, device_code)
        await registry.remove_device(session, device, vehicle, services.commands)
        await session.commit()


@router.put("/devices/{device_code}/config", status_code=202)
async def set_config(device_code: str, body: ConfigBody, caller: Caller = Depends(any_caller),
                     session_factory: Any = Depends(get_session_factory), services: Any = Depends(get_services)):
    require_roles(caller, *ENGINEER)
    async with session_factory() as session:
        device, vehicle = await _device_and_vehicle(session, caller, device_code)
        cmd = await services.commands.set_config(session, caller, device, vehicle, body.model_dump(exclude_none=True))
        await session.commit()
        command_id, version = cmd.command_id, device.config_version
    state = await services.commands.deliver(command_id)
    return {"commandId": command_id, "configVersion": version, "state": state}


@router.post("/devices/{device_code}/commands", status_code=202)
async def send_command(device_code: str, body: CommandBody, caller: Caller = Depends(any_caller),
                       session_factory: Any = Depends(get_session_factory), services: Any = Depends(get_services)):
    require_roles(caller, *ENGINEER)
    async with session_factory() as session:
        device, vehicle = await _device_and_vehicle(session, caller, device_code)
        cmd = await services.commands.simple(session, caller, device, vehicle, body.command.upper(), body.params)
        await session.commit()
        command_id = cmd.command_id
    state = await services.commands.deliver(command_id)
    return {"commandId": command_id, "state": state}


@router.get("/devices/{device_code}/commands")
async def command_history(device_code: str, limit: int = Query(default=20, ge=1, le=100),
                          caller: Caller = Depends(any_caller), session_factory: Any = Depends(get_session_factory)):
    require_roles(caller, *STAFF_READ)
    async with session_factory() as session:
        device, _ = await _device_and_vehicle(session, caller, device_code)
        rows = (await session.execute(select(DeviceCommand).where(DeviceCommand.device_id == device.device_id)
                                      .order_by(DeviceCommand.command_id.desc()).limit(limit))).scalars().all()
    return [command_dto(c, device.device_code) for c in rows]


@router.get("/commands/{command_id}")
async def get_command(command_id: int, caller: Caller = Depends(any_caller),
                      session_factory: Any = Depends(get_session_factory)):
    require_roles(caller, *STAFF_READ)
    async with session_factory() as session:
        cmd = await session.get(DeviceCommand, command_id)
        if cmd is None or (caller.tenant_id is not None and cmd.tenant_id != caller.tenant_id):
            raise NotFound("Command not found")
        device = await session.get(Device, cmd.device_id) if cmd.device_id else None
    return command_dto(cmd, device.device_code if device else None)
