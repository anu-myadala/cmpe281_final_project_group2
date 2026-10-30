"""DashboardService: the overview aggregate (cached 30 s per tenant), the
vehicle map (positions cached 10 s per tenant) and the statistics (BR-08)."""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import ClientError
from ..container import AppContainer
from ..core.rbac import Scope, vehicles_of_scope
from ..core.timeutil import iso, utcnow
from ..errors import BadGateway, BusinessRule
from ..models import ALERT_TYPES, OPEN_STATUSES, sound_class_name
from ..repos import AlertSummaryRepo

ONLINE_WINDOW = timedelta(seconds=30)
HOUR_RANGE = timedelta(days=2)
DAY_RANGE = timedelta(days=92)


def pick_granularity(from_: datetime, to: datetime, requested: str = "auto") -> str:
    """BR-08: up to 2 days by hour, up to 92 days by day, longer refused."""
    span = to - from_
    if span > DAY_RANGE:
        raise BusinessRule("BR-08", "Range too long: at most 92 days", field="to", maxDays=92)
    if requested in ("hour", "day"):
        return requested
    return "hour" if span <= HOUR_RANGE else "day"


def bucket_start(ts: datetime, granularity: str) -> datetime:
    if granularity == "hour":
        return ts.replace(minute=0, second=0, microsecond=0)
    return ts.replace(hour=0, minute=0, second=0, microsecond=0)


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing in degrees from point 1 to point 2."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class DashboardService:
    def __init__(self, container: AppContainer, session: AsyncSession) -> None:
        self.c = container
        self.session = session

    # -- overview -----------------------------------------------------------------------------
    async def overview(self, scope: Scope) -> Dict[str, Any]:
        cache = self.c.overview_cache
        cached = cache.get(scope.tenant_id)
        if cached is not None:
            return cached
        vehicles_task = self.c.vehicle_client.list(scope.tenant_id)
        alerts_task = self.c.alert_client.open_alerts(scope.tenant_id, 10)
        vehicles_result, alerts_result = await asyncio.gather(vehicles_task, alerts_task, return_exceptions=True)
        failures = [r for r in (vehicles_result, alerts_result) if isinstance(r, Exception)]
        if failures:
            for failure in failures:
                if not isinstance(failure, ClientError):
                    raise failure
            stale = cache.get_stale(scope.tenant_id)
            if stale is not None:
                return {**stale, "degraded": True}
            if not self.c.settings.overview_local_fallback:
                raise BadGateway("C2 or C3 did not answer and no cached overview exists",
                                 {"errors": [str(f) for f in failures]})
            # Opt-in developer fallback: the C2/C3 tables the seed fills, read directly.
            if isinstance(vehicles_result, Exception):
                vehicles_result = await self._local_vehicles(scope)
            if isinstance(alerts_result, Exception):
                alerts_result = await self._local_open_alerts(scope)
            dto = await self._build_overview(scope, vehicles_result, alerts_result)
            return {**dto, "degraded": True}
        dto = await self._build_overview(scope, vehicles_result, alerts_result)
        cache.put(scope.tenant_id, dto)
        return dto

    async def _local_vehicles(self, scope: Scope) -> List[Dict[str, Any]]:
        """Vehicle cards from the vehicle table and the newest telemetry sample (C2 unavailable)."""
        now = utcnow()
        positions = await self.c.events.latest_positions(scope.tenant_code)
        cards = []
        for v in await vehicles_of_scope(self.session, scope):
            sample = (positions.get(v.vehicle_code) or {}).get("latest")
            ts = sample.get("ts") if sample else None
            connection = "NEVER" if ts is None else ("ONLINE" if now - ts < ONLINE_WINDOW else "OFFLINE")
            cards.append({"vehicleId": v.vehicle_code, "vin": v.vin, "label": v.label, "status": v.status,
                          "connection": connection, "lastSeenAt": iso(ts), "retentionDays": v.retention_days,
                          "deviceSummary": None})
        return cards

    async def _local_open_alerts(self, scope: Scope) -> Tuple[List[Dict[str, Any]], int]:
        """Open alerts from the C3 view (C3 unavailable)."""
        rows = await AlertSummaryRepo.rows(self.session, scope.tenant_id, utcnow() - timedelta(days=3650),
                                           utcnow() + timedelta(days=3650), statuses=list(OPEN_STATUSES))
        rows = sorted(rows, key=lambda r: r["created_at"], reverse=True)
        items = [{"alertId": r["alert_id"], "vehicleId": r["vehicle_id"], "type": r["alert_type_code"],
                  "severity": r["severity"], "status": r["status"], "topClass": sound_class_name(r["top_class_id"]),
                  "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
                  "createdAt": iso(r["created_at"])} for r in rows[:10]]
        return items, len(rows)

    async def _build_overview(self, scope: Scope, vehicles: List[Dict[str, Any]],
                              alerts: Tuple[List[Dict[str, Any]], int]) -> Dict[str, Any]:
        now = utcnow()
        day_ago = now - timedelta(days=1)
        open_alerts, open_total = alerts
        if scope.vehicle_ids is not None:  # RIDER: restrict to the vehicles of the active trip
            allowed = {v.vehicle_code for v in await vehicles_of_scope(self.session, scope, include_retired=True)}
            vehicles = [v for v in vehicles if v.get("vehicleId") in allowed]
            open_alerts = [a for a in open_alerts if a.get("vehicleId") in allowed]
            open_total = len(open_alerts)
        codes = [v.get("vehicleId") for v in vehicles]
        activity = await self.c.events.vehicle_activity(scope.tenant_code, day_ago, now)
        events_24h = await self.c.events.count_events(scope.tenant_code, day_ago, now, vehicle_codes=codes)
        alerts_24h = await AlertSummaryRepo.count(self.session, scope.tenant_id, day_ago, now,
                                                  vehicle_codes=codes if scope.vehicle_ids is not None else None)
        open_per_vehicle: Dict[str, int] = {}
        for a in open_alerts:
            open_per_vehicle[a.get("vehicleId")] = open_per_vehicle.get(a.get("vehicleId"), 0) + 1
        if open_total > len(open_alerts):  # the list is capped at 10; count the rest from the view
            open_per_vehicle = await AlertSummaryRepo.open_count_per_vehicle(self.session, scope.tenant_id)
        cards = []
        for v in vehicles:
            code = v.get("vehicleId")
            act = activity.get(code, {})
            cards.append({"vehicleId": code, "vin": v.get("vin"), "label": v.get("label"), "status": v.get("status"),
                          "connection": v.get("connection"), "lastSeenAt": v.get("lastSeenAt"),
                          "deviceSummary": v.get("deviceSummary"),
                          "lastEventAt": iso(act.get("lastEventAt")), "openAlerts": open_per_vehicle.get(code, 0),
                          "topClass24h": act.get("topClass")})
        online = sum(1 for v in vehicles if v.get("connection") == "ONLINE")
        recent = sorted(open_alerts, key=lambda a: a.get("createdAt") or "", reverse=True)[:10]
        return {
            "tenant": {"tenantId": scope.tenant_id, "name": scope.tenant_name, "plan": scope.plan},
            "kpis": {"vehicles": len(vehicles), "online": online, "openAlerts": open_total,
                     "alerts24h": alerts_24h, "events24h": events_24h},
            "vehicles": cards,
            "recentAlerts": [{k: a.get(k) for k in ("alertId", "vehicleId", "type", "severity", "status", "topClass",
                                                     "confidence", "createdAt")} for a in recent],
            "asOf": iso(now),
            "degraded": False,
        }

    # -- vehicle map -----------------------------------------------------------------------------
    async def map(self, scope: Scope) -> Dict[str, Any]:
        now = utcnow()
        vehicles = await vehicles_of_scope(self.session, scope)
        positions = self.c.map_cache.get(scope.tenant_code)
        if positions is None:
            positions = await self.c.events.latest_positions(scope.tenant_code)
            self.c.map_cache.put(scope.tenant_code, positions)
        cars = [self._car_point(v, positions.get(v.vehicle_code), now) for v in vehicles]
        degraded = False
        pins = self.c.pins_cache.get(scope.tenant_id)
        if pins is None:
            try:
                pins = await self.c.alert_client.map_points(scope.tenant_id)
                self.c.pins_cache.put(scope.tenant_id, pins)
            except ClientError:
                pins = self.c.pins_cache.get_stale(scope.tenant_id) or []
                degraded = True
        if scope.vehicle_ids is not None:
            allowed = {v.vehicle_code for v in vehicles}
            pins = [p for p in pins if p.get("vehicleId") in allowed]
        alerts = [{k: p.get(k) for k in ("alertId", "vehicleId", "lat", "lon", "severity", "type", "createdAt")}
                  for p in pins]
        return {"vehicles": cars, "alerts": alerts, "asOf": iso(now), "degraded": degraded}

    @staticmethod
    def _car_point(vehicle, sample: Optional[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
        point = {"vehicleId": vehicle.vehicle_code, "label": vehicle.label, "status": vehicle.status,
                 "connection": None, "lat": None, "lon": None, "headingDeg": None, "speedKph": None,
                 "driveMode": None, "ignition": None, "sampleAt": None}
        if not sample or not sample.get("latest"):
            return point
        latest = sample["latest"]
        coords = (latest.get("location") or {}).get("coordinates") or [None, None]
        ts = latest.get("ts")
        if isinstance(ts, datetime) and ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
        point.update({"lon": coords[0], "lat": coords[1], "speedKph": latest.get("speedKph"),
                      "driveMode": latest.get("driveMode"), "ignition": latest.get("ignition"), "sampleAt": iso(ts),
                      "connection": "ONLINE" if ts is not None and now - ts < ONLINE_WINDOW else "OFFLINE"})
        previous = sample.get("previous")
        if previous and point["lat"] is not None:
            pcoords = (previous.get("location") or {}).get("coordinates") or [None, None]
            if pcoords[0] is not None and distance_m(pcoords[1], pcoords[0], point["lat"], point["lon"]) > 1.0:
                point["headingDeg"] = round(bearing(pcoords[1], pcoords[0], point["lat"], point["lon"]), 1)
        return point

    # -- statistics -------------------------------------------------------------------------------
    async def stats(self, scope: Scope, from_: datetime, to: datetime, granularity: str = "auto") -> Dict[str, Any]:
        granularity = pick_granularity(from_, to, granularity)
        codes = None
        if scope.vehicle_ids is not None:
            codes = [v.vehicle_code for v in await vehicles_of_scope(self.session, scope, include_retired=True)]
        rows, false_alarm_rate, mean_ack = await AlertSummaryRepo.stats(self.session, scope.tenant_id, from_, to, codes)
        buckets: Dict[datetime, Dict[str, int]] = {}
        for r in rows:
            key = bucket_start(r["created_at"], granularity)
            entry = buckets.setdefault(key, {t: 0 for t in ALERT_TYPES})
            if r["alert_type_code"] in entry:
                entry[r["alert_type_code"]] += 1
        per_period = [{"period": iso(k), **buckets[k]} for k in sorted(buckets)]
        events = await self.c.events.events_by_class(scope.tenant_code, from_, to, vehicle_codes=codes)
        device_status = {"ONLINE": 0, "OFFLINE": 0, "FAULT": 0, "PROVISIONING": 0, "DISABLED": 0}
        try:
            for v in await self.c.vehicle_client.list(scope.tenant_id):
                summary = v.get("deviceSummary") or {}
                known = 0
                for key in ("online", "offline", "fault", "disabled"):
                    n = int(summary.get(key) or 0)
                    device_status[key.upper()] += n
                    known += n
                device_status["PROVISIONING"] += max(int(summary.get("total") or 0) - known, 0)
        except ClientError:
            device_status["degraded"] = True
        return {"granularity": granularity, "from": iso(from_), "to": iso(to),
                "alertsByTypePerPeriod": per_period,
                "eventsByClass": [{"class": e["class"], "count": e["count"]} for e in events],
                "deviceStatus": device_status, "falseAlarmRate": false_alarm_rate, "meanAckSeconds": mean_ack,
                "totals": {"alerts": len(rows), "open": sum(1 for r in rows if r["status"] in OPEN_STATUSES),
                           "events": sum(e["count"] for e in events)}}
