"""M5 Status monitor (Section 4.5 of the C2 design, FC2): telemetry and status messages from the
cars, the sweep every 10 s, the state machine of the device, history and device.status events.

    TelemetryIngestor.on_telemetry  one vehicle_telemetry document + heartbeats (BR-C2-18)
    StatusTracker.on_status         status online / offline (last will) / device fault / ack
    StatusTracker.sweep             OFFLINE after 30 s without a heartbeat (BR-C2-19)
    decide_status                   the decision table of Section 4.5 as a pure function
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select

from ..models import Device, Vehicle
from ..timeutil import as_naive_utc, utcnow
from .publisher import safe_publish
from .support import status_payload

log = logging.getLogger("scicp.c2.status")


def decide_status(d: Device, now: datetime, offline_after_s: int, car_lost: bool) -> str:
    """Section 4.5, Table 34: checked from the top, the first match decides."""
    if d.status == "DISABLED":
        return "DISABLED"                                   # S1
    if d.last_seen_at is None:
        return "PROVISIONING"                               # S2
    if car_lost or (now - as_naive_utc(d.last_seen_at)).total_seconds() > offline_after_s:
        return "OFFLINE"                                    # S3
    if d.reported_ok is False:
        return "FAULT"                                      # S4
    return "ONLINE"                                         # S5, S6


def _reason(old: str, new: str, car_lost: bool) -> str:
    if new == "OFFLINE":
        return "LAST_WILL" if car_lost else "TIMEOUT"
    if new == "FAULT":
        return "FAULT"
    return "HEARTBEAT"


class StatusTracker:
    def __init__(self, session_factory: Any, mongo: Any, publisher: Any, tenants: Any, settings: Any,
                 commands: Any = None) -> None:
        self.session_factory = session_factory
        self.mongo = mongo
        self.publisher = publisher
        self.tenants = tenants
        self.settings = settings
        self.commands = commands             # CommandService, set after construction (on_ack, resend)
        self.lost: Set[int] = set()          # vehicle ids whose last will arrived, until the next heartbeat

    async def _vehicle(self, session, tenant_code: str, vehicle_code: str) -> Optional[Vehicle]:
        tenant_id = await self.tenants.id_of(tenant_code)
        if tenant_id is None:
            return None
        return (await session.execute(select(Vehicle).where(Vehicle.tenant_id == tenant_id,
                                                            Vehicle.vehicle_code == vehicle_code))).scalars().first()

    async def _apply(self, session, vehicle: Vehicle, devices: List[Device], now: datetime) -> Tuple[list, bool]:
        """Evaluate each device; returns the (device, old, new, reason) changes and whether the gateway came online."""
        changes = []
        gateway_online = False
        car_lost = vehicle.vehicle_id in self.lost
        for d in devices:
            new = decide_status(d, now, self.settings.c2_offline_after_s, car_lost)
            if new != d.status:
                changes.append((d, d.status, new, _reason(d.status, new, car_lost)))
                if d.device_type == "EDGE_GATEWAY" and new == "ONLINE":
                    gateway_online = True
                d.status = new
        if gateway_online and vehicle.status == "PROVISIONED":
            vehicle.status = "ACTIVE"                       # BR-C2-21: the first contact makes the car ACTIVE
        return changes, gateway_online

    async def _after_commit(self, vehicle: Vehicle, changes: list, gateway_online: bool, now: datetime) -> None:
        tenant_code = await self.tenants.code_of(vehicle.tenant_id)
        if changes:
            docs = [{"tenantId": tenant_code, "vehicleId": vehicle.vehicle_code, "deviceId": d.device_code, "ts": now,
                     "from": old, "to": new, "reason": reason, "lastSeenAt": d.last_seen_at}
                    for d, old, new, reason in changes]
            try:
                await self.mongo["device_state_history"].insert_many(docs)
            except Exception as exc:  # noqa: BLE001 - history is best effort
                log.warning("device_state_history insert failed: %s", exc)
            for d, old, new, reason in changes:
                log.info("device %s on %s: %s -> %s (%s)", d.device_code, vehicle.vehicle_code, old, new, reason)
                await safe_publish(self.publisher, "device.status", status_payload(d, vehicle.vehicle_code),
                                   vehicle.tenant_id)
        if gateway_online and self.commands is not None:
            await self.commands.resend_pending(vehicle.vehicle_id)

    async def _devices(self, session, vehicle_id: int) -> List[Device]:
        return list((await session.execute(select(Device).where(Device.vehicle_id == vehicle_id,
                                                                Device.deleted_at.is_(None)))).scalars())

    # -- inputs -----------------------------------------------------------------------------------------
    async def heartbeat(self, tenant_code: str, vehicle_code: str, items: List[Dict[str, Any]],
                        now: Optional[datetime] = None) -> int:
        """Telemetry: the gateway and every listed device were seen now (BR-C2-18, BR-C2-20)."""
        now = now or utcnow()
        async with self.session_factory() as session:
            vehicle = await self._vehicle(session, tenant_code, vehicle_code)
            if vehicle is None or vehicle.status == "RETIRED":
                return 0
            self.lost.discard(vehicle.vehicle_id)
            devices = await self._devices(session, vehicle.vehicle_id)
            by_code = {d.device_code: d for d in devices}
            for d in devices:
                if d.device_type == "EDGE_GATEWAY":
                    d.last_seen_at, d.reported_ok = now, True
            for item in items or []:
                d = by_code.get(str(item.get("deviceId")))
                if d is None or d.device_type == "EDGE_GATEWAY":
                    continue
                d.last_seen_at = now
                d.reported_ok = bool(item.get("ok", True))
                d.fault_reason = None if d.reported_ok else str(item.get("reason") or "NOT_OK")[:120]
                if "muted" in item and d.status != "DISABLED":
                    d.muted = bool(item["muted"])
            changes, gateway_online = await self._apply(session, vehicle, devices, now)
            await session.commit()
        await self._after_commit(vehicle, changes, gateway_online, now)
        return len(changes)

    async def on_status(self, tenant_code: str, vehicle_code: str, msg: Dict[str, Any]) -> None:
        kind = msg.get("type", "agent")
        if kind == "ack":
            if self.commands is not None:
                await self.commands.on_ack(tenant_code, vehicle_code, msg)
            return
        now = utcnow()
        async with self.session_factory() as session:
            vehicle = await self._vehicle(session, tenant_code, vehicle_code)
            if vehicle is None or vehicle.status == "RETIRED":
                return
            devices = await self._devices(session, vehicle.vehicle_id)
            if kind == "agent":
                if msg.get("state") == "offline":
                    self.lost.add(vehicle.vehicle_id)       # the last will: OFFLINE at once (BR-C2-19)
                else:
                    self.lost.discard(vehicle.vehicle_id)
                    for d in devices:
                        if d.device_type == "EDGE_GATEWAY":
                            d.last_seen_at, d.reported_ok = now, True
            elif kind == "device":
                d = next((x for x in devices if x.device_code == msg.get("deviceId")), None)
                if d is not None:
                    d.reported_ok = bool(msg.get("ok", True))
                    d.fault_reason = None if d.reported_ok else str(msg.get("reason") or "NOT_OK")[:120]
                    d.last_seen_at = now
            changes, gateway_online = await self._apply(session, vehicle, devices, now)
            await session.commit()
        await self._after_commit(vehicle, changes, gateway_online, now)

    async def sweep(self, now: Optional[datetime] = None) -> int:
        """Devices ONLINE or FAULT not seen for 30 s become OFFLINE (index ix_device_sweep)."""
        now = now or utcnow()
        cutoff = now - timedelta(seconds=self.settings.c2_offline_after_s)
        async with self.session_factory() as session:
            stale = list((await session.execute(select(Device).where(
                Device.status.in_(("ONLINE", "FAULT")), Device.last_seen_at < cutoff,
                Device.deleted_at.is_(None)))).scalars())
            if not stale:
                return 0
            vehicles = {v.vehicle_id: v for v in (await session.execute(
                select(Vehicle).where(Vehicle.vehicle_id.in_({d.vehicle_id for d in stale})))).scalars()}
            per_vehicle: Dict[int, list] = {}
            for d in stale:
                per_vehicle.setdefault(d.vehicle_id, []).append(d)
            results = []
            for vehicle_id, devices in per_vehicle.items():
                changes, _ = await self._apply(session, vehicles[vehicle_id], devices, now)
                results.append((vehicles[vehicle_id], changes))
            await session.commit()
        for vehicle, changes in results:
            await self._after_commit(vehicle, changes, False, now)
        return sum(len(c) for _, c in results)

    async def admin_change(self, device: Device, vehicle_code: str, tenant_id: int, old: str) -> None:
        """Enable, disable, delete from the API: history and device.status (the caller committed)."""
        if old == device.status:
            return
        tenant_code = await self.tenants.code_of(tenant_id)
        try:
            await self.mongo["device_state_history"].insert_one({
                "tenantId": tenant_code, "vehicleId": vehicle_code, "deviceId": device.device_code, "ts": utcnow(),
                "from": old, "to": device.status, "reason": "ADMIN", "lastSeenAt": device.last_seen_at})
        except Exception as exc:  # noqa: BLE001
            log.warning("device_state_history insert failed: %s", exc)
        await safe_publish(self.publisher, "device.status", status_payload(device, vehicle_code), tenant_id)


class TelemetryIngestor:
    """Writes the telemetry message to vehicle_telemetry (the shape the seed and the map use) and
    hands the device list to the StatusTracker as heartbeats."""

    def __init__(self, mongo: Any, tracker: StatusTracker) -> None:
        self.mongo = mongo
        self.tracker = tracker

    async def on_telemetry(self, tenant_code: str, vehicle_code: str, msg: Dict[str, Any]) -> None:
        now = utcnow()
        doc = {"ts": now, "meta": {"tenantId": tenant_code, "vehicleId": vehicle_code},
               "speedKph": msg.get("speedKph"), "driveMode": msg.get("driveMode"), "ignition": msg.get("ignition"),
               "location": msg.get("location"), "network": msg.get("network"), "agent": msg.get("agent"),
               "devices": msg.get("devices") or []}
        try:
            await self.mongo["vehicle_telemetry"].insert_one(doc)
        except Exception as exc:  # noqa: BLE001
            log.warning("vehicle_telemetry insert failed for %s: %s", vehicle_code, exc)
        await self.tracker.heartbeat(tenant_code, vehicle_code, msg.get("devices") or [], now)
