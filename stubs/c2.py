"""C2 stub: vehicles, devices and telemetry (Part II, Tables of Section 2),
served from the seeded vehicle table and the vehicle_telemetry collection.

Mounted at /c2/api/v1 (inline) or /api/v1 on :8002 (standalone).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from app.core.timeutil import parse_iso
from app.errors import NotFound, Validation
from app.models import Vehicle

from .common import (Caller, StubContext, as_naive_utc, connection_of, device_ids, devices_for, get_ctx, latest_samples,
                     page_params, resolve_caller, telemetry_dto, utcnow, vehicle_dto)

c2_router = APIRouter(tags=["c2-stub"])


async def _caller(request: Request, tenantId: Optional[str] = Query(default=None),
                  ctx: StubContext = Depends(get_ctx)) -> Caller:
    return await resolve_caller(request, ctx, tenantId, require_tenant=False)


async def _vehicles(ctx: StubContext, caller: Caller, include_retired: bool = False) -> List[Vehicle]:
    async with ctx.session_factory() as session:
        stmt = select(Vehicle).order_by(Vehicle.vehicle_code)
        if caller.tenant_id is not None:
            stmt = stmt.where(Vehicle.tenant_id == caller.tenant_id)
        if not include_retired:
            stmt = stmt.where(Vehicle.status != "RETIRED")
        rows = list((await session.execute(stmt)).scalars().all())
    if caller.rider_vehicle_codes is not None:
        rows = [v for v in rows if v.vehicle_code in caller.rider_vehicle_codes]
    return rows


async def _vehicle(ctx: StubContext, caller: Caller, vehicle_code: str) -> Vehicle:
    async with ctx.session_factory() as session:
        stmt = select(Vehicle).where(Vehicle.vehicle_code == vehicle_code)
        if caller.tenant_id is not None:
            stmt = stmt.where(Vehicle.tenant_id == caller.tenant_id)
        vehicle = (await session.execute(stmt)).scalars().first()
    if vehicle is None or (caller.rider_vehicle_codes is not None and vehicle_code not in caller.rider_vehicle_codes):
        raise NotFound("Vehicle not found")
    if caller.tenant_code is None:
        caller.tenant_id = vehicle.tenant_id
        caller.tenant_code = await ctx.tenant_code(vehicle.tenant_id)
    return vehicle


@c2_router.get("/vehicles")
async def list_vehicles(status: Optional[str] = None, connection: Optional[str] = None, q: Optional[str] = None,
                        page: Optional[int] = None, size: Optional[int] = None,
                        caller: Caller = Depends(_caller), ctx: StubContext = Depends(get_ctx)):
    """{items[{vehicleId, vin, label, make, model, year, status, connection, lastSeenAt, retentionDays,
    deviceSummary{total, online, offline, fault, disabled}}], page, size, total}"""
    if caller.tenant_id is None:
        raise Validation("tenantId is required", {"field": "tenantId"})
    page, size = page_params(page, size)
    now = utcnow()
    vehicles = await _vehicles(ctx, caller, include_retired=status == "RETIRED")
    samples = await latest_samples(ctx.mongo, caller.tenant_code)
    items = [vehicle_dto(v, samples.get(v.vehicle_code), now) for v in vehicles]
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


@c2_router.get("/vehicles/{vehicle_code}")
async def get_vehicle(vehicle_code: str, caller: Caller = Depends(_caller), ctx: StubContext = Depends(get_ctx)):
    vehicle = await _vehicle(ctx, caller, vehicle_code)
    samples = await latest_samples(ctx.mongo, caller.tenant_code, [vehicle_code])
    dto = vehicle_dto(vehicle, samples.get(vehicle_code))
    dto["devices"] = devices_for(vehicle_code, vehicle.status, dto["connection"],
                                 samples.get(vehicle_code, {}).get("ts") if samples.get(vehicle_code) else None)
    return dto


@c2_router.get("/vehicles/{vehicle_code}/devices")
async def list_devices(vehicle_code: str, caller: Caller = Depends(_caller), ctx: StubContext = Depends(get_ctx)):
    """[{deviceId, type, status, lastSeenAt}] - a stable list per car whose status follows the connection."""
    vehicle = await _vehicle(ctx, caller, vehicle_code)
    samples = await latest_samples(ctx.mongo, caller.tenant_code, [vehicle_code])
    sample = samples.get(vehicle_code)
    ts = sample.get("ts") if sample else None
    return devices_for(vehicle_code, vehicle.status, connection_of(ts), ts)


@c2_router.get("/vehicles/{vehicle_code}/telemetry")
async def telemetry(vehicle_code: str, limit: int = Query(default=60, ge=1, le=1000),
                    from_: Optional[str] = Query(default=None, alias="from"), to: Optional[str] = None,
                    caller: Caller = Depends(_caller), ctx: StubContext = Depends(get_ctx)):
    """Newest first: [{ts, speedKph, driveMode, ignition, location{type, coordinates:[lon, lat]}, network, ...}]"""
    await _vehicle(ctx, caller, vehicle_code)
    match: Dict[str, Any] = {"meta.tenantId": caller.tenant_code, "meta.vehicleId": vehicle_code}
    window: Dict[str, Any] = {}
    if from_:
        window["$gte"] = parse_iso(from_, "from")
    if to:
        window["$lt"] = parse_iso(to, "to")
    if window:
        match["ts"] = window
    cursor = ctx.mongo["vehicle_telemetry"].find(match).sort("ts", -1).limit(limit)
    docs = await cursor.to_list(limit)
    return [telemetry_dto(d) for d in docs]


@c2_router.get("/devices")
async def list_all_devices(caller: Caller = Depends(_caller), ctx: StubContext = Depends(get_ctx)):
    """Flat device list of the tenant (the IoT engineer's device page reads it)."""
    vehicles = await _vehicles(ctx, caller, include_retired=True)
    samples = await latest_samples(ctx.mongo, caller.tenant_code)
    now = utcnow()
    out: List[Dict[str, Any]] = []
    for v in vehicles:
        sample = samples.get(v.vehicle_code)
        ts = as_naive_utc(sample.get("ts")) if sample else None
        out.extend(devices_for(v.vehicle_code, v.status, connection_of(ts, now), ts, now))
    return {"items": out, "total": len(out)}


__all__ = ["c2_router", "device_ids"]
