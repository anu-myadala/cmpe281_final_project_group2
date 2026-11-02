"""Demo event generator: keeps the online cars moving, flips a microphone
to FAULT and back now and then, and raises a new alert every few minutes.
It also runs the simulated C4 classifier worker (stubs.worker) next to it:
new audio events carry the model version the worker has loaded, so an
activation in the registry shows up on the alert detail page.

    await demo_generator(eventbus, mongo_db, mysql_url)

`eventbus` is anything with ``async publish(topic, payload)``: the C1
EventBus in inline mode, the HttpPublisher (POST /internal/events) in the
standalone stubs container. The samples and rows it writes are the same
ones the C2/C3 stubs and the dashboard read, so the map, the alert monitor
and the statistics stay consistent during the live demo.

Environment:
  DEMO_ENABLED=1                 0 disables the generator
  DEMO_TICK_S=10                 telemetry period per online car
  DEMO_ALERT_PERIOD_S=150        mean period between generated alerts (+/- 20 %)
  DEMO_MAX_ALERTS=30             cap per process so a long session does not flood the queue
  DEMO_DEVICE_FAULT_MEAN_S=60    mean period between device.status FAULT messages
  DEMO_ONLINE_VEHICLES=AV-01,AV-10,AV-11,AV-12
  WORKER_ENABLED=1               0 runs the generator without the classifier worker
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from bson import ObjectId
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.timeutil import iso
from app.db import make_engine
from app.models import Alert, AlertAction, Tenant, Vehicle, sound_class_id

from .common import ALERT_RULES, DEVICE_OVERRIDES, device_ids, env_flag, utcnow
from .worker import ClassifierWorker

_REPO_ROOT = Path(__file__).resolve().parents[1]
try:
    from seed.seed import PATHS, MODEL_VERSION_TAG, _event_doc, point_along_loop
except ImportError:  # developer checkout without the repository root on sys.path
    sys.path.insert(0, str(_REPO_ROOT))
    from seed.seed import PATHS, MODEL_VERSION_TAG, _event_doc, point_along_loop  # type: ignore

log = logging.getLogger("scicp.demo")

DEFAULT_ONLINE = "AV-01,AV-10,AV-11,AV-12"
ALERT_CLASS_POOL = ["siren"] * 3 + ["honking"] * 3 + ["glass_break"] * 2 + ["scream"] * 2 + ["crash"]
FAULT_REASONS = ["NO_SIGNAL", "CLIPPING", "LOW_LEVEL", "USB_RESET"]


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# -------------------------------------------------------------------------------------------
# Geometry: where on its loop is the car, so the next sample continues from there
# -------------------------------------------------------------------------------------------
def _flat(lat: float, lon: float, ref_lat: float) -> tuple:
    k = 111320.0
    return (lon * k * math.cos(math.radians(ref_lat)), lat * k)


def loop_progress(path: List[tuple], lat: float, lon: float) -> float:
    """Meters along the closed loop of the point on it closest to (lat, lon)."""
    ref_lat = path[0][0]
    p = _flat(lat, lon, ref_lat)
    best_dist, best_progress, before = float("inf"), 0.0, 0.0
    for i in range(len(path)):
        a, b = path[i], path[(i + 1) % len(path)]
        fa, fb = _flat(a[0], a[1], ref_lat), _flat(b[0], b[1], ref_lat)
        dx, dy = fb[0] - fa[0], fb[1] - fa[1]
        length = math.hypot(dx, dy)
        if length == 0:
            continue
        t = max(0.0, min(1.0, ((p[0] - fa[0]) * dx + (p[1] - fa[1]) * dy) / (length * length)))
        proj = (fa[0] + t * dx, fa[1] + t * dy)
        dist = math.hypot(p[0] - proj[0], p[1] - proj[1])
        if dist < best_dist:
            best_dist, best_progress = dist, before + t * length
        before += length
    return best_progress


# -------------------------------------------------------------------------------------------
# Per-car state
# -------------------------------------------------------------------------------------------
class CarState:
    def __init__(self, code: str, tenant_id: int, tenant_code: str, rng: random.Random) -> None:
        self.code = code
        self.tenant_id = tenant_id
        self.tenant_code = tenant_code
        self.number = int(code.split("-")[-1])
        self.path = PATHS.get(code) or PATHS["AV-01"]
        self.progress = rng.uniform(0, 1500)
        self.speed_kph = rng.uniform(20, 35)
        self.lat, self.lon = point_along_loop(self.path, self.progress)
        self.devices = list(device_ids(code).keys())

    def continue_from(self, sample: Optional[Dict[str, Any]]) -> None:
        coords = ((sample or {}).get("location") or {}).get("coordinates")
        if coords and coords[0] is not None:
            self.lon, self.lat = coords[0], coords[1]
            self.progress = loop_progress(self.path, self.lat, self.lon)
            if sample and isinstance(sample.get("speedKph"), (int, float)):
                self.speed_kph = float(sample["speedKph"])

    def step(self, dt_s: float, rng: random.Random) -> Dict[str, Any]:
        roll = rng.random()
        if roll < 0.06:
            self.speed_kph = 0.0  # a stop at a light
        elif roll < 0.25:
            self.speed_kph = rng.uniform(8, 20)
        else:
            self.speed_kph = min(50.0, max(12.0, self.speed_kph + rng.uniform(-6, 6)))
        self.progress += self.speed_kph / 3.6 * dt_s
        self.lat, self.lon = point_along_loop(self.path, self.progress)
        ts = utcnow()
        mics = [d for d in self.devices if not d.endswith("10")]
        return {
            "ts": ts,
            "meta": {"tenantId": self.tenant_code, "vehicleId": self.code},
            "speedKph": round(self.speed_kph, 1),
            "driveMode": "AUTONOMOUS" if self.number != 1 else ("MANUAL" if rng.random() < 0.2 else "AUTONOMOUS"),
            "ignition": True,
            "location": {"type": "Point", "coordinates": [round(self.lon, 6), round(self.lat, 6)]},
            "network": ({"type": "CELLULAR", "rssiDbm": rng.randint(-95, -70)} if self.number != 1 else
                        {"type": "WIFI", "ssid": "Nguyen-Home", "rssiDbm": rng.randint(-70, -50)}),
            "agent": {"version": "0.4.1", "uptimeS": 4200 + int(ts.timestamp()) % 86400, "cpuPct": rng.randint(8, 25),
                      "outboxDepth": 0},
            "devices": [{"deviceId": d, "ok": DEVICE_OVERRIDES.get(d) is None, "muted": False,
                         "levelDb": round(rng.uniform(35, 62), 1)} for d in mics],
        }


# -------------------------------------------------------------------------------------------
# The generator
# -------------------------------------------------------------------------------------------
async def _load_cars(session_factory, codes: List[str], rng: random.Random) -> List[CarState]:
    async with session_factory() as session:
        rows = (await session.execute(
            select(Vehicle.vehicle_code, Vehicle.tenant_id, Tenant.code).join(Tenant, Tenant.tenant_id == Vehicle.tenant_id)
            .where(Vehicle.vehicle_code.in_(codes), Vehicle.status != "RETIRED"))).all()
    return [CarState(code, tenant_id, tenant_code, rng) for code, tenant_id, tenant_code in rows]


async def _create_alert(session_factory, mongo_db, publisher, car: CarState, rng: random.Random,
                        model_tag: str = MODEL_VERSION_TAG) -> Optional[int]:
    cls = rng.choice(ALERT_CLASS_POOL)
    state = "MOVING" if car.speed_kph > 0 else "PARKED"
    rule = next((r for r in ALERT_RULES if r["class"] == cls and r["vehicleState"] in ("ANY", state)), None)
    if rule is None:  # crash while parked has no rule: pick the siren rule instead
        cls, rule = "siren", ALERT_RULES[0]
    confidence = round(rng.uniform(rule["minConfidence"] + 0.05, 0.97), 3)
    now = utcnow()
    event_id = ObjectId()
    channel = "interior" if cls == "scream" else "exterior"
    doc = _event_doc(event_id, car.tenant_code, car.code, now - timedelta(seconds=4), channel, cls, confidence,
                     round(car.lat, 6), round(car.lon, 6), state, rng,
                     {"alert": True, "alertId": None, "alertType": rule["alertType"], "severity": rule["severity"],
                      "reason": "ALERT_CREATED"})
    doc["context"]["speedKph"] = round(car.speed_kph, 1)
    doc["inference"]["modelVersion"] = model_tag  # the version the classifier worker runs now
    await mongo_db["audio_events"].insert_one(doc)
    async with session_factory() as session:
        row = Alert(tenant_id=car.tenant_id, vehicle_id=car.code, alert_type_code=rule["alertType"], rule_id=rule["ruleId"],
                    top_class_id=sound_class_id(cls), severity=rule["severity"], status="NEW", event_ref=str(event_id),
                    confidence=confidence, vehicle_state=state, model_version=model_tag,
                    count=rule["minRepeats"], lat=round(car.lat, 6), lon=round(car.lon, 6), created_at=now, updated_at=now)
        session.add(row)
        await session.flush()
        session.add(AlertAction(alert_id=row.alert_id, user_id=None, action="CREATE", from_status=None, to_status="NEW",
                                ts=now))
        await session.commit()
        alert_id = row.alert_id
    await mongo_db["audio_events"].update_one({"_id": event_id}, {"$set": {"decision.alertId": alert_id}})
    alert = {"alertId": alert_id, "vehicleId": car.code, "type": rule["alertType"], "severity": rule["severity"],
             "status": "NEW", "topClass": cls, "confidence": confidence, "createdAt": iso(now), "ageSeconds": 0,
             "acknowledgedBy": None, "lat": round(car.lat, 6), "lon": round(car.lon, 6), "vehicleState": state,
             "eventRef": str(event_id)}
    await publisher.publish("alert.created", {"tenantId": car.tenant_id, "alert": alert})
    log.info("demo: alert %s %s/%s %s %.2f on %s (%s, %s) -> alert.created", alert_id, rule["alertType"],
             rule["severity"], cls, confidence, car.code, car.tenant_code, model_tag)
    return alert_id


def _make_worker(eventbus: Any) -> Optional[ClassifierWorker]:
    if not env_flag("WORKER_ENABLED", "1"):
        log.info("classifier worker disabled (WORKER_ENABLED=0)")
        return None
    from app.config import Settings  # the same secrets as the API (.env or the environment)
    # Inline mode hands over the C1 EventBus (it has subscribe); the stubs container hands over
    # the HTTP publisher, and the worker listens on MQTT instead.
    bus = eventbus if hasattr(eventbus, "subscribe") else None
    return ClassifierWorker.from_env(bus=bus, service_token_secret=Settings().service_token_secret)


async def demo_generator(eventbus: Any, mongo_db: Any, mysql_url: str, publisher: Any = None,
                         worker: Optional[ClassifierWorker] = None) -> None:
    """Runs until canceled. See the module docstring for the environment knobs."""
    worker = worker or _make_worker(eventbus)
    worker_task = asyncio.create_task(worker.run()) if worker is not None else None
    try:
        if not env_flag("DEMO_ENABLED", "1"):
            log.info("demo generator disabled (DEMO_ENABLED=0)")
            if worker_task is not None:
                await worker_task
            return
        await _generate(eventbus, mongo_db, mysql_url, publisher, worker)
    finally:
        if worker_task is not None:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


async def _generate(eventbus: Any, mongo_db: Any, mysql_url: str, publisher: Any,
                    worker: Optional[ClassifierWorker]) -> None:
    publisher = publisher or eventbus
    tick_s = max(2.0, _env_float("DEMO_TICK_S", 10.0))
    alert_period_s = max(20.0, _env_float("DEMO_ALERT_PERIOD_S", 150.0))
    max_alerts = _env_int("DEMO_MAX_ALERTS", 30)
    fault_mean_s = max(tick_s, _env_float("DEMO_DEVICE_FAULT_MEAN_S", 60.0))
    codes = [c.strip() for c in os.environ.get("DEMO_ONLINE_VEHICLES", DEFAULT_ONLINE).split(",") if c.strip()]
    rng = random.Random()

    engine = make_engine(mysql_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    telemetry = mongo_db["vehicle_telemetry"]
    cars: List[CarState] = []
    alerts_created = 0
    next_alert_at = utcnow() + timedelta(seconds=alert_period_s * rng.uniform(0.4, 0.8))
    pending_recoveries: Dict[str, Dict[str, Any]] = {}
    try:
        # Wait for the seed (the tables and the online cars) before the first tick.
        for _ in range(60):
            try:
                cars = await _load_cars(session_factory, codes, rng)
            except Exception as exc:  # noqa: BLE001 - the database may still be starting
                log.info("demo generator waiting for the database: %s", exc)
                cars = []
            if cars:
                break
            await asyncio.sleep(5)
        if not cars:
            log.warning("demo generator: none of %s found in the vehicle table; giving up", codes)
            return
        for car in cars:
            latest = await telemetry.find({"meta.vehicleId": car.code, "meta.tenantId": car.tenant_code}) \
                .sort("ts", -1).limit(1).to_list(1)
            car.continue_from(latest[0] if latest else None)
        log.info("demo generator: %d cars (%s), telemetry every %.0f s, alerts every ~%.0f s (max %d)",
                 len(cars), ", ".join(c.code for c in cars), tick_s, alert_period_s, max_alerts)

        while True:
            now = utcnow()
            # 1. telemetry: one new sample per online car
            docs = [car.step(tick_s, rng) for car in cars]
            try:
                await telemetry.insert_many(docs)
            except Exception as exc:  # noqa: BLE001
                log.warning("demo generator: telemetry insert failed: %s", exc)

            # 2. device recoveries that are due, then maybe a new fault (not in the same tick as a recovery)
            recovered = False
            for device_id, info in list(pending_recoveries.items()):
                if info["until"] <= now:
                    DEVICE_OVERRIDES.pop(device_id, None)
                    pending_recoveries.pop(device_id, None)
                    recovered = True
                    await publisher.publish("device.status", {"tenantId": info["tenantId"], "vehicleId": info["vehicleId"],
                                                              "deviceId": device_id, "status": "ONLINE",
                                                              "lastSeenAt": iso(now)})
                    log.info("demo: device %s on %s back ONLINE -> device.status", device_id, info["vehicleId"])
            if not recovered and rng.random() < tick_s / fault_mean_s and len(pending_recoveries) < 2:
                car = rng.choice(cars)
                mic = rng.choice([d for d in car.devices if not d.endswith("10")])
                if mic not in pending_recoveries:
                    reason = rng.choice(FAULT_REASONS)
                    until = now + timedelta(seconds=rng.uniform(25, 50))
                    DEVICE_OVERRIDES[mic] = {"status": "FAULT", "faultReason": reason, "until": until}
                    pending_recoveries[mic] = {"until": until, "tenantId": car.tenant_id, "vehicleId": car.code}
                    await publisher.publish("device.status", {"tenantId": car.tenant_id, "vehicleId": car.code,
                                                              "deviceId": mic, "status": "FAULT", "faultReason": reason,
                                                              "lastSeenAt": iso(now), "muted": False})
                    log.info("demo: device %s on %s FAULT (%s) -> device.status", mic, car.code, reason)

            # 3. a new alert now and then
            if now >= next_alert_at and alerts_created < max_alerts:
                try:
                    tag = (worker.model_tag if worker is not None else None) or MODEL_VERSION_TAG
                    await _create_alert(session_factory, mongo_db, publisher, rng.choice(cars), rng, tag)
                    alerts_created += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning("demo generator: alert creation failed: %s", exc)
                next_alert_at = now + timedelta(seconds=alert_period_s * rng.uniform(0.8, 1.2))
                if alerts_created >= max_alerts:
                    log.info("demo generator: DEMO_MAX_ALERTS=%d reached; no more alerts this run", max_alerts)
            await asyncio.sleep(tick_s)
    except asyncio.CancelledError:
        raise
    finally:
        await engine.dispose()


__all__ = ["demo_generator", "loop_progress"]
