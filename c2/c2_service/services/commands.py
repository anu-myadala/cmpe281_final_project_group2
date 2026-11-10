"""M4 Configuration and commands, and the delivery of M8 network settings (Section 4.4 of the C2
design, BR-C2-11 to BR-C2-13). Every message to a car is a device_command row:

    PENDING -> SENT -> APPLIED | REJECTED | FAILED
    SENT -> TIMED_OUT (30 s without ack) -> SENT again once when the car reconnects
    PENDING / SENT / TIMED_OUT -> SUPERSEDED (a newer SET_CONFIG for the device)

The device keeps the confirmed values (config_json) apart from the requested ones
(desired_config_json), so a page never shows a value the car has not applied.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from ..auth import Caller
from ..errors import BusinessRule, NotImplementedYet, Validation
from ..models import MIC_TYPES, Device, DeviceCommand, NetworkConfig, Vehicle
from ..timeutil import iso, utcnow
from .publisher import safe_publish
from .support import status_payload

log = logging.getLogger("scicp.c2.commands")

OPEN = ("PENDING", "SENT", "TIMED_OUT")
GAIN_RANGE = (-12, 24)
THRESHOLD_RANGE = (0.10, 0.95)
DEFAULT_MIC_CONFIG = {"micGainDb": 6, "detectionThreshold": 0.40}


def check_config_values(values: Dict[str, Any]) -> Dict[str, Any]:
    """BR-C2-12: gain a whole number -12..+24, threshold 0.10..0.95 in 0.01 steps, at least one;
    sampleRateHz and clipSeconds are fixed and refused with another value."""
    out: Dict[str, Any] = {}
    if values.get("sampleRateHz") not in (None, 16000):
        raise BusinessRule("BR-C2-12", "The sample rate is fixed at 16000 Hz", field="sampleRateHz")
    if values.get("clipSeconds") not in (None, 5):
        raise BusinessRule("BR-C2-12", "The clip length is fixed at 5 s", field="clipSeconds")
    if values.get("micGainDb") is not None:
        gain = values["micGainDb"]
        if isinstance(gain, bool) or not isinstance(gain, (int, float)) or float(gain) != int(gain) \
                or not GAIN_RANGE[0] <= int(gain) <= GAIN_RANGE[1]:
            raise BusinessRule("BR-C2-12", "micGainDb must be a whole number from -12 to +24", field="micGainDb")
        out["micGainDb"] = int(gain)
    if values.get("detectionThreshold") is not None:
        thr = values["detectionThreshold"]
        if isinstance(thr, bool) or not isinstance(thr, (int, float)) \
                or not THRESHOLD_RANGE[0] - 1e-9 <= float(thr) <= THRESHOLD_RANGE[1] + 1e-9 \
                or abs(round(float(thr), 2) - float(thr)) > 1e-9:
            raise BusinessRule("BR-C2-12", "detectionThreshold must be 0.10 to 0.95 in steps of 0.01",
                               field="detectionThreshold")
        out["detectionThreshold"] = round(float(thr), 2)
    if not out:
        raise Validation("Give micGainDb or detectionThreshold", {"field": "micGainDb"})
    return out


def command_dto(c: DeviceCommand, device_code: Optional[str] = None) -> Dict[str, Any]:
    return {"commandId": c.command_id, "command": c.command, "deviceId": device_code, "params": c.params_json,
            "status": c.status, "attempts": c.attempts, "issuedBy": c.issued_by, "createdAt": iso(c.created_at),
            "sentAt": iso(c.sent_at), "ackedAt": iso(c.acked_at), "result": c.result_json}


class CommandService:
    def __init__(self, session_factory: Any, gateway: Any, publisher: Any, tenants: Any, settings: Any,
                 secretbox: Any = None) -> None:
        self.session_factory = session_factory
        self.gateway = gateway              # MqttGateway (publish), or a fake in the tests
        self.publisher = publisher
        self.tenants = tenants
        self.settings = settings
        self.secretbox = secretbox

    # -- requests from the API ------------------------------------------------------------------------
    @staticmethod
    def check_allowed(device: Device, vehicle: Vehicle, command: str) -> None:
        """BR-C2-11."""
        if vehicle.status in ("REGISTERED", "RETIRED"):
            raise BusinessRule("BR-C2-11", f"The car is {vehicle.status}: provision it first" if vehicle.status ==
                               "REGISTERED" else "The car is retired", field="vehicleId")
        if device.status == "DISABLED":
            raise BusinessRule("BR-C2-11", "The device is disabled", field="deviceId")
        if command in ("SET_CONFIG", "MUTE", "UNMUTE") and device.device_type not in MIC_TYPES:
            raise BusinessRule("BR-C2-11", f"{command} applies to microphones only", field="deviceId")
        if command == "RESTART_AGENT" and device.device_type != "EDGE_GATEWAY":
            raise BusinessRule("BR-C2-11", "RESTART_AGENT applies to the edge gateway only", field="deviceId")
        if command == "FIRMWARE_UPDATE":
            raise NotImplementedYet("FIRMWARE_UPDATE is design only this semester")

    async def set_config(self, session, caller: Caller, device: Device, vehicle: Vehicle,
                         values: Dict[str, Any]) -> DeviceCommand:
        self.check_allowed(device, vehicle, "SET_CONFIG")
        changes = check_config_values(values)
        base = dict(device.desired_config_json or device.config_json or DEFAULT_MIC_CONFIG)
        new = {**base, **changes}
        await self._supersede(session, device.device_id, "SET_CONFIG")
        device.config_version = (device.config_version or 1) + 1
        device.desired_config_json = new
        device.config_state = "PENDING"
        cmd = DeviceCommand(tenant_id=vehicle.tenant_id, vehicle_id=vehicle.vehicle_id, device_id=device.device_id,
                            issued_by=caller.user_id, command="SET_CONFIG", status="PENDING",
                            params_json={"version": device.config_version, "settings": new}, created_at=utcnow())
        session.add(cmd)
        await session.flush()
        return cmd

    async def simple(self, session, caller: Caller, device: Device, vehicle: Vehicle, command: str,
                     params: Optional[dict] = None) -> DeviceCommand:
        if command not in ("MUTE", "UNMUTE", "RESTART_AGENT", "FIRMWARE_UPDATE"):
            raise Validation("command must be MUTE, UNMUTE, RESTART_AGENT or FIRMWARE_UPDATE", {"field": "command"})
        self.check_allowed(device, vehicle, command)
        cmd = DeviceCommand(tenant_id=vehicle.tenant_id, vehicle_id=vehicle.vehicle_id, device_id=device.device_id,
                            issued_by=caller.user_id if caller else None, command=command, status="PENDING",
                            params_json=params or {}, created_at=utcnow())
        session.add(cmd)
        await session.flush()
        return cmd

    async def network(self, session, caller: Caller, vehicle: Vehicle, profiles: List[dict]) -> DeviceCommand:
        await self._supersede(session, None, "SET_NETWORK", vehicle.vehicle_id)
        cmd = DeviceCommand(tenant_id=vehicle.tenant_id, vehicle_id=vehicle.vehicle_id, device_id=None,
                            issued_by=caller.user_id, command="SET_NETWORK", status="PENDING",
                            params_json={"profiles": profiles}, created_at=utcnow())
        session.add(cmd)
        await session.flush()
        return cmd

    async def _supersede(self, session, device_id: Optional[int], command: str, vehicle_id: Optional[int] = None) -> None:
        stmt = select(DeviceCommand).where(DeviceCommand.command == command, DeviceCommand.status.in_(OPEN))
        stmt = stmt.where(DeviceCommand.device_id == device_id) if device_id is not None else \
            stmt.where(DeviceCommand.vehicle_id == vehicle_id)
        for old in (await session.execute(stmt)).scalars():
            old.status = "SUPERSEDED"

    async def supersede_all(self, session, vehicle_id: int, device_id: Optional[int] = None) -> None:
        """Retire, disable or delete: open commands become SUPERSEDED (BR-C2-04)."""
        stmt = select(DeviceCommand).where(DeviceCommand.vehicle_id == vehicle_id, DeviceCommand.status.in_(OPEN))
        if device_id is not None:
            stmt = stmt.where(DeviceCommand.device_id == device_id)
        for old in (await session.execute(stmt)).scalars():
            old.status = "SUPERSEDED"

    # -- delivery -------------------------------------------------------------------------------------
    async def deliver(self, command_id: int) -> str:
        """Send one command if its car is online (after the request committed); returns its status."""
        async with self.session_factory() as session:
            cmd = await session.get(DeviceCommand, command_id)
            if cmd is None:
                return "UNKNOWN"
            status = await self._send(session, cmd)
            await session.commit()
            return status

    async def _send(self, session, cmd: DeviceCommand) -> str:
        if cmd.status not in ("PENDING", "TIMED_OUT") or cmd.attempts >= 2:
            return cmd.status
        vehicle = await session.get(Vehicle, cmd.vehicle_id)
        gateway = (await session.execute(select(Device).where(Device.vehicle_id == cmd.vehicle_id,
                                                              Device.device_type == "EDGE_GATEWAY",
                                                              Device.deleted_at.is_(None)))).scalars().first()
        if vehicle is None or gateway is None or gateway.status not in ("ONLINE", "FAULT"):
            return cmd.status                               # stays PENDING until the car is back
        device = await session.get(Device, cmd.device_id) if cmd.device_id else None
        tenant_code = await self.tenants.code_of(vehicle.tenant_id)
        message: Dict[str, Any] = {"commandId": cmd.command_id, "type": cmd.command, "ts": iso(utcnow())}
        if device is not None:
            message["deviceId"] = device.device_code
        if cmd.command == "SET_CONFIG":
            message["version"] = (cmd.params_json or {}).get("version")
            message["settings"] = (cmd.params_json or {}).get("settings")
            topic = "config"
        elif cmd.command == "SET_NETWORK":
            message["profiles"] = await self._network_profiles(session, vehicle.vehicle_id)
            topic = "config"
        else:
            topic = "command"
        # Record SENT and commit *before* publishing: the car can ack within a millisecond, and on_ack
        # (another session) must already see SENT or it drops the ack as unknown (Table 33, A1).
        before = (cmd.status, cmd.sent_at, cmd.attempts)
        cmd.status, cmd.sent_at, cmd.attempts = "SENT", utcnow(), (cmd.attempts or 0) + 1
        await session.commit()
        ok = await self.gateway.publish(f"scicp/v1/{tenant_code}/{vehicle.vehicle_code}/{topic}", message, qos=1)
        if not ok:
            cmd.status, cmd.sent_at, cmd.attempts = before
            await session.commit()
            return cmd.status
        log.info("command %s %s sent to %s (attempt %s)", cmd.command_id, cmd.command, vehicle.vehicle_code, cmd.attempts)
        return cmd.status

    async def _network_profiles(self, session, vehicle_id: int) -> List[dict]:
        rows = (await session.execute(select(NetworkConfig).where(NetworkConfig.vehicle_id == vehicle_id)
                                      .order_by(NetworkConfig.priority))).scalars()
        out = []
        for r in rows:
            item = {"priority": r.priority, "ssid": r.ssid, "securityType": r.security_type}
            if r.password_enc and self.secretbox is not None:
                item["password"] = self.secretbox.open(r.password_enc)   # only inside the car's TLS session
            out.append(item)
        return out

    async def resend_pending(self, vehicle_id: int) -> int:
        """The gateway is ONLINE again: send PENDING commands and TIMED_OUT ones once more (BR-C2-13)."""
        async with self.session_factory() as session:
            cmds = list((await session.execute(select(DeviceCommand).where(
                DeviceCommand.vehicle_id == vehicle_id, DeviceCommand.status.in_(("PENDING", "TIMED_OUT")),
                DeviceCommand.attempts < 2).order_by(DeviceCommand.command_id))).scalars())
            sent = 0
            for cmd in cmds:
                if cmd.command == "SET_CONFIG" and cmd.device_id:      # before sending: the ack may be instant
                    device = await session.get(Device, cmd.device_id)
                    if device is not None and device.config_state == "TIMED_OUT":
                        device.config_state = "PENDING"
                if await self._send(session, cmd) == "SENT":
                    sent += 1
            await session.commit()
        return sent

    # -- answers from the car ---------------------------------------------------------------------------
    async def on_ack(self, tenant_code: str, vehicle_code: str, msg: Dict[str, Any]) -> Optional[str]:
        """Section 4.4, Table 33: a late or unknown ack is ignored."""
        try:
            command_id = int(msg.get("commandId"))
        except (TypeError, ValueError):
            return None
        result = str(msg.get("result", "")).upper()
        if result not in ("APPLIED", "REJECTED", "FAILED"):
            return None
        async with self.session_factory() as session:
            cmd = await session.get(DeviceCommand, command_id)
            if cmd is None or cmd.status not in ("SENT", "TIMED_OUT"):
                return None                                  # A1
            vehicle = await session.get(Vehicle, cmd.vehicle_id)
            if vehicle is None or vehicle.vehicle_code != vehicle_code or \
                    await self.tenants.code_of(vehicle.tenant_id) != tenant_code:
                log.warning("ack for command %s from the wrong car %s/%s ignored", command_id, tenant_code, vehicle_code)
                return None
            cmd.status, cmd.acked_at = result, utcnow()
            cmd.result_json = {k: v for k, v in msg.items() if k not in ("commandId",)}
            device = await session.get(Device, cmd.device_id) if cmd.device_id else None
            config = None
            if device is not None and cmd.command == "SET_CONFIG":
                version = (cmd.params_json or {}).get("version")
                if result == "APPLIED":
                    reported = msg.get("reported") or {}
                    device.config_json = {k: reported.get(k, v) for k, v in ((cmd.params_json or {}).get("settings") or {}).items()}
                    device.config_state = "CONFIRMED"
                    device.desired_config_json = None
                else:
                    device.config_state = "REJECTED"
                config = {"version": version, "state": device.config_state, "commandId": cmd.command_id}
                if result != "APPLIED" and msg.get("reason"):
                    config["reason"] = str(msg["reason"])[:200]
            if device is not None and cmd.command in ("MUTE", "UNMUTE") and result == "APPLIED":
                device.muted = cmd.command == "MUTE"
            await session.commit()
        if device is not None:
            await safe_publish(self.publisher, "device.status", status_payload(device, vehicle.vehicle_code, config),
                               vehicle.tenant_id)
        log.info("command %s %s: %s", command_id, cmd.command, result)
        return result

    async def timeouts(self) -> int:
        """SENT for more than 30 s without an ack: TIMED_OUT (runs every 5 s)."""
        cutoff = utcnow() - timedelta(seconds=self.settings.c2_command_timeout_s)
        events = []
        async with self.session_factory() as session:
            cmds = list((await session.execute(select(DeviceCommand).where(
                DeviceCommand.status == "SENT", DeviceCommand.sent_at < cutoff))).scalars())
            for cmd in cmds:
                cmd.status = "TIMED_OUT"
                if cmd.device_id:
                    device = await session.get(Device, cmd.device_id)
                    vehicle = await session.get(Vehicle, cmd.vehicle_id)
                    if device is not None and cmd.command == "SET_CONFIG":
                        device.config_state = "TIMED_OUT"
                        events.append((device, vehicle, {"version": (cmd.params_json or {}).get("version"),
                                                         "state": "TIMED_OUT", "commandId": cmd.command_id}))
            await session.commit()
        for device, vehicle, config in events:
            await safe_publish(self.publisher, "device.status", status_payload(device, vehicle.vehicle_code, config),
                               vehicle.tenant_id)
        return len(cmds)
