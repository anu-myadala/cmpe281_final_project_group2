"""SubscriptionService (BR-04): one card per vehicle with one entry per
intelligence service; save validates the whole card against the decision
table of Part I Table 27 before writing anything, then upserts the rows and
the SUBSCRIPTION_SAVE audit row in one transaction. defaults(vehicleId) is
the hook C2 calls when a vehicle is created."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..container import AppContainer
from ..core.audit import AuditLogger, snapshot
from ..core.rbac import Scope, vehicle_in_scope, vehicles_of_scope
from ..core.timeutil import iso, parse_time, time_str, utcnow
from ..errors import BusinessRule, NotFound
from ..models import IntelligenceService, Subscription, Tenant, Vehicle
from ..repos import IntelligenceServiceRepo, SubscriptionRepo, TenantRepo
from ..schemas import SubscriptionBody

PLAN_CHANNELS = {
    "BASIC": {"DASHBOARD", "EMAIL"},
    "FLEET": {"DASHBOARD", "EMAIL", "SMS", "PUSH"},
    "ENTERPRISE": {"DASHBOARD", "EMAIL", "SMS", "PUSH"},
}
DEFAULT_SERVICES = ("EMERGENCY", "ACCIDENT", "SECURITY", "SAFETY")
DEFAULT_CHANNELS = "DASHBOARD,EMAIL"
CHANNEL_ORDER = ("DASHBOARD", "EMAIL", "SMS", "PUSH")


def _channels_str(channels) -> str:
    chosen = set(channels) | {"DASHBOARD"}
    return ",".join(c for c in CHANNEL_ORDER if c in chosen)


def service_entry(row: Optional[Subscription], service: IntelligenceService) -> Dict[str, Any]:
    if row is None:
        return {"serviceCode": service.service_code, "name": service.name, "available": service.available,
                "enabled": False, "channels": ["DASHBOARD"], "quietFrom": None, "quietTo": None}
    return {"serviceCode": row.service_code, "name": service.name, "available": service.available,
            "enabled": bool(row.enabled), "channels": row.channel_list,
            "quietFrom": time_str(row.quiet_from), "quietTo": time_str(row.quiet_to)}


class SubscriptionService:
    def __init__(self, container: AppContainer, session: AsyncSession) -> None:
        self.c = container
        self.session = session

    async def _card(self, vehicle: Vehicle, rows: List[Subscription],
                    services: List[IntelligenceService]) -> Dict[str, Any]:
        by_code = {r.service_code: r for r in rows}
        return {"vehicleId": vehicle.vehicle_code, "label": vehicle.label,
                "services": [service_entry(by_code.get(s.service_code), s) for s in services],
                "notifyRiders": any(r.notify_riders for r in rows),
                "updatedAt": iso(max((r.updated_at for r in rows), default=None))}

    async def list(self, scope: Scope, vehicle_code: Optional[str] = None) -> List[Dict[str, Any]]:
        vehicles = await vehicles_of_scope(self.session, scope)
        if vehicle_code:
            vehicles = [v for v in vehicles if v.vehicle_code == vehicle_code]
            if not vehicles:
                raise NotFound("Vehicle not found")
        services = await IntelligenceServiceRepo.list(self.session)
        rows = await SubscriptionRepo.by_vehicles(self.session, [v.vehicle_id for v in vehicles])
        return [await self._card(v, rows.get(v.vehicle_id, []), services) for v in vehicles]

    async def save(self, scope: Scope, vehicle_code: str, dto: SubscriptionBody, actor: int,
                   ip: Optional[str] = None) -> Dict[str, Any]:
        vehicle = await vehicle_in_scope(self.session, scope, vehicle_code)
        plan = scope.plan
        services = await IntelligenceServiceRepo.list(self.session)
        known = {s.service_code: s for s in services}
        available = {code for code, s in known.items() if s.available}
        # Decision table of Section 4.3, applied per service row before any write.
        cleaned = []
        for s in dto.services:
            if s.serviceCode not in known:
                raise BusinessRule("BR-04", f"Unknown service {s.serviceCode}", field=f"{s.serviceCode}.serviceCode")
            if s.enabled and s.serviceCode not in available:  # R1
                raise BusinessRule("BR-04", f"Service {s.serviceCode} is not available on this platform",
                                   field=f"{s.serviceCode}.enabled")
            if not set(s.channels) <= PLAN_CHANNELS[plan]:  # R2
                raise BusinessRule("BR-04", f"Channels not allowed by the {plan} plan",
                                   field=f"{s.serviceCode}.channels", allowed=sorted(PLAN_CHANNELS[plan]))
            quiet_from = parse_time(s.quietFrom, f"{s.serviceCode}.quietFrom")
            quiet_to = parse_time(s.quietTo, f"{s.serviceCode}.quietTo")
            if (quiet_from is None) != (quiet_to is None) or (quiet_from is not None and quiet_from == quiet_to):  # R3
                raise BusinessRule("BR-04", "Quiet hours need both times and they must differ",
                                   field=f"{s.serviceCode}.quietHours")
            cleaned.append((s, quiet_from, quiet_to, _channels_str(s.channels)))  # R4: DASHBOARD always on
        before_rows = await SubscriptionRepo.by_vehicle(self.session, vehicle.vehicle_id)
        before = [snapshot(r) for r in before_rows]
        by_code = {r.service_code: r for r in before_rows}
        now = utcnow()
        for s, quiet_from, quiet_to, channels in cleaned:
            row = by_code.get(s.serviceCode)
            if row is None:
                row = Subscription(tenant_id=scope.tenant_id, vehicle_id=vehicle.vehicle_id, service_code=s.serviceCode)
                self.session.add(row)
                by_code[s.serviceCode] = row
            row.enabled = s.enabled
            row.channels = channels
            row.quiet_from = quiet_from
            row.quiet_to = quiet_to
            row.updated_by = actor
            row.updated_at = now
        for row in by_code.values():  # notify_riders is a per-vehicle flag stored on every row
            row.notify_riders = dto.notifyRiders
            row.updated_by = actor
        await self.session.flush()
        after_rows = await SubscriptionRepo.by_vehicle(self.session, vehicle.vehicle_id)
        AuditLogger.write(self.session, actor, scope.tenant_id, "SUBSCRIPTION_SAVE", "subscription",
                          vehicle.vehicle_code, before, [snapshot(r) for r in after_rows], ip)
        await self.session.commit()
        return await self._card(vehicle, after_rows, services)

    async def defaults(self, vehicle: Vehicle) -> List[Subscription]:
        """Default rows for a new vehicle: the four available services with DASHBOARD and EMAIL (idempotent)."""
        existing = {r.service_code for r in await SubscriptionRepo.by_vehicle(self.session, vehicle.vehicle_id)}
        created = []
        for code in DEFAULT_SERVICES:
            if code in existing:
                continue
            row = Subscription(tenant_id=vehicle.tenant_id, vehicle_id=vehicle.vehicle_id, service_code=code,
                               enabled=True, channels=DEFAULT_CHANNELS, notify_riders=False)
            self.session.add(row)
            created.append(row)
        if created:
            AuditLogger.write(self.session, None, vehicle.tenant_id, "SUBSCRIPTION_SAVE", "subscription",
                              vehicle.vehicle_code, None, {"defaults": list(DEFAULT_SERVICES)})
        await self.session.commit()
        return created

    async def internal_rows(self, tenant_id: int, vehicle_code: Optional[str] = None) -> List[Dict[str, Any]]:
        """The rows C3 reads at alert time (BR-05), with the tenant time zone."""
        tenant: Optional[Tenant] = await TenantRepo.get(self.session, tenant_id)
        if tenant is None:
            raise NotFound("Tenant not found")
        rows = await SubscriptionRepo.by_tenant(self.session, tenant_id)
        vehicles = {v.vehicle_id: v for v in await vehicles_of_scope(
            self.session, Scope(tenant.tenant_id, tenant.code, tenant.name, tenant.plan, tenant.time_zone,
                                "SERVICE_STAFF", 0), include_retired=True)}
        out = []
        for r in rows:
            vehicle = vehicles.get(r.vehicle_id)
            if vehicle is None or (vehicle_code and vehicle.vehicle_code != vehicle_code):
                continue
            out.append({"tenantId": tenant.tenant_id, "tenantCode": tenant.code, "timeZone": tenant.time_zone,
                        "vehicleId": vehicle.vehicle_code, "serviceCode": r.service_code, "enabled": bool(r.enabled),
                        "channels": r.channel_list, "quietFrom": time_str(r.quiet_from),
                        "quietTo": time_str(r.quiet_to), "notifyRiders": bool(r.notify_riders)})
        return out
