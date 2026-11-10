"""Idempotent seed of the SCICP demo data (docs/BRIEF.md, "Seed data").

Usage as a CLI (reads DATABASE_URL, MONGO_URL and STORAGE_BACKEND with its variables):

    python -m seed.seed

Usage as a module (the API calls this at startup when SEED_ON_START=1):

    await seed.run(engine_or_url, mongo_db, storage=None)

What it writes:
  MySQL   tenant, app_user, intelligence_service, alert_type, vehicle, vehicle_rider,
          subscription, alert, alert_action, ai_model, model_version, feedback_label
  MongoDB audio_events (~400 documents over 14 days), vehicle_telemetry (10 min of
          samples per online car around SJSU; AV-13 stopped 14 min ago; AV-14 none)
  storage the two sound-classifier artifacts of seed/models (local volume or S3), when it is reachable
Every row has a natural key, so running the seed twice changes nothing except
the telemetry samples, which are rebuilt relative to "now" for the demo.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import random
import sys
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from bson import ObjectId

# The seed reuses the models and the schema creation of the C1 API package.
_API_DIR = Path(__file__).resolve().parents[1] / "c1" / "api"
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from sqlalchemy import select, delete, func  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker  # noqa: E402

from app.db import create_schema, make_engine, make_mongo  # noqa: E402
from app.models import (AiModel, Alert, AlertAction, AlertType, AppUser, FeedbackLabel, IntelligenceService,  # noqa: E402
                        ModelVersion, Subscription, Tenant, Vehicle, VehicleRider, sound_class_id)

log = logging.getLogger("scicp.seed")

DEMO_PASSWORD = "Passw0rd!demo"
SJSU = (37.3352, -121.8811)
MODEL_VERSION_TAG = "sound-classifier-1.1.0"
CLASSES = ("siren", "crash", "glass_break", "scream", "honking", "traffic", "environmental")

# Default alert rules of Part III Table 95: (class, vehicle state) -> (rule_id, alert type)
RULES = {
    ("siren", "MOVING"): (1, "EMERGENCY"), ("siren", "PARKED"): (1, "EMERGENCY"),
    ("crash", "MOVING"): (2, "ACCIDENT"),
    ("glass_break", "MOVING"): (3, "ACCIDENT"), ("glass_break", "PARKED"): (4, "SECURITY"),
    ("scream", "PARKED"): (5, "SECURITY"), ("scream", "MOVING"): (6, "EMERGENCY"),
    ("honking", "MOVING"): (7, "SAFETY"), ("honking", "PARKED"): (7, "SAFETY"),
}
ALERT_TYPES = [
    ("ACCIDENT", "Accident", "CRITICAL", 10, "Crash or glass break while the car is moving"),
    ("EMERGENCY", "Emergency", "HIGH", 5, "Siren or scream near the car"),
    ("SECURITY", "Security", "HIGH", 15, "Glass break or scream while the car is parked"),
    ("SAFETY", "Safety", "MEDIUM", 30, "Repeated honking around the car"),
    ("PASSENGER", "Passenger", "LOW", 60, "Passenger well-being (design only)"),
    ("VEHICLE_HEALTH", "Vehicle health", "LOW", 120, "Unusual mechanical sounds (design only)"),
]
SERVICES = [
    ("EMERGENCY", "Emergency detection", "Sirens and screams", True),
    ("ACCIDENT", "Accident detection", "Crash and glass break while moving", True),
    ("SECURITY", "Security monitoring", "Glass break and screams while parked", True),
    ("SAFETY", "Safety monitoring", "Repeated honking", True),
    ("PASSENGER", "Passenger well-being", "Design only", False),
    ("VEHICLE_HEALTH", "Vehicle health", "Design only", False),
]
TENANTS = [
    dict(tenant_id=1, code="tA", name="Nguyen household", tenant_type="OWNER", plan="BASIC", max_vehicles=3),
    dict(tenant_id=2, code="tB", name="Bay Shuttle LLC", tenant_type="FLEET", plan="FLEET", max_vehicles=50),
]
USERS = [
    dict(user_id=1001, email="anh@example.com", name="Anh Nguyen", role="OWNER", tenant_id=1),
    dict(user_id=1002, email="maria@bayshuttle.example", name="Maria Lopez", role="FLEET_MANAGER", tenant_id=2),
    dict(user_id=1003, email="rider@example.com", name="Rider Demo", role="RIDER", tenant_id=1),
    dict(user_id=1004, email="iot@scicp.example", name="D. Patel", role="IOT_ENGINEER", tenant_id=None),
    dict(user_id=1005, email="staff@scicp.example", name="Service Staff", role="SERVICE_STAFF", tenant_id=None),
    dict(user_id=1006, email="admin@scicp.example", name="Platform Admin", role="SYS_ADMIN", tenant_id=None),
]
VEHICLES = [
    dict(vehicle_id=1, tenant_id=1, vehicle_code="AV-01", vin="1HGCM82633A004352", make="Honda", model="Civic",
         model_year=2021, label="Anh's Civic", status="ACTIVE", online=True),
    dict(vehicle_id=10, tenant_id=2, vehicle_code="AV-10", vin="5YJ3E1EA7KF300010", make="Tesla", model="Model 3",
         model_year=2023, label="Shuttle 1", status="ACTIVE", online=True),
    dict(vehicle_id=11, tenant_id=2, vehicle_code="AV-11", vin="5YJ3E1EA7KF300011", make="Tesla", model="Model 3",
         model_year=2023, label="Shuttle 2", status="ACTIVE", online=True),
    dict(vehicle_id=12, tenant_id=2, vehicle_code="AV-12", vin="5YJ3E1EA7KF300012", make="Tesla", model="Model 3",
         model_year=2023, label="Shuttle 3", status="ACTIVE", online=True),
    dict(vehicle_id=13, tenant_id=2, vehicle_code="AV-13", vin="5YJ3E1EA7KF300013", make="Tesla", model="Model 3",
         model_year=2024, label="Shuttle 4", status="ACTIVE", online=False),   # telemetry stopped 14 min ago
    dict(vehicle_id=14, tenant_id=2, vehicle_code="AV-14", vin="5YJ3E1EA7KF300014", make="Tesla", model="Model 3",
         model_year=2024, label="Shuttle 5", status="REGISTERED", online=None),  # never sent telemetry
]
TENANT_CODE = {1: "tA", 2: "tB"}
TENANT_USER = {1: 1001, 2: 1002}

# Street loops around SJSU (lat, lon), each car drives its own block.
PATHS = {
    "AV-01": [(37.3382, -121.8855), (37.3382, -121.8775), (37.3330, -121.8775), (37.3330, -121.8855)],
    "AV-10": [(37.3366, -121.8905), (37.3366, -121.8855), (37.3340, -121.8855), (37.3340, -121.8905)],
    "AV-11": [(37.3395, -121.8905), (37.3395, -121.8800), (37.3382, -121.8800), (37.3382, -121.8905)],
    "AV-12": [(37.3382, -121.8775), (37.3382, -121.8700), (37.3310, -121.8700), (37.3310, -121.8775)],
    "AV-13": [(37.3330, -121.8855), (37.3330, -121.8775), (37.3290, -121.8775), (37.3290, -121.8855)],
}

# The two sound-classifier versions are real C4 artifacts (seed/models): 1.1.0 is CLAP zero-shot with text
# prompts, 1.2.0 a small head trained on CLAP embeddings of the course Smart Car audio dataset. Both were
# scored on the same held-out clips; seed/models/evaluation.json holds the scores, the split and the test
# files, and embed_clips.py / train_and_score.py rebuild them.
MODELS_DIR = Path(__file__).resolve().parent / "models"
ARTIFACTS = {version: (MODELS_DIR / version / "model.pt").read_bytes() for version in ("1.1.0", "1.2.0")}
EVALUATION = json.loads((MODELS_DIR / "evaluation.json").read_text())


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def oid(seed: str) -> ObjectId:
    """Deterministic 24-hex ObjectId derived from a name (keeps the seed idempotent)."""
    return ObjectId(hashlib.sha1(seed.encode()).hexdigest()[:24])



# -------------------------------------------------------------------------------------------
# Geometry for the telemetry paths
# -------------------------------------------------------------------------------------------
def _dist_m(a, b) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    d = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(d))


def point_along_loop(path: List[tuple], meters: float) -> tuple:
    legs = [(path[i], path[(i + 1) % len(path)]) for i in range(len(path))]
    lengths = [_dist_m(a, b) for a, b in legs]
    total = sum(lengths)
    s = meters % total
    for (a, b), length in zip(legs, lengths):
        if s <= length:
            f = s / length if length else 0
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
        s -= length
    return path[0]


def telemetry_samples(code: str, tenant_code: str, start: datetime, end: datetime, rng: random.Random,
                      interval_s: int = 10) -> List[Dict[str, Any]]:
    """Samples every 10 s along the car's loop with a varying, plausible speed."""
    path = PATHS[code]
    offset = rng.uniform(0, 2000)  # each car starts somewhere else on its loop
    number = int(code.split("-")[1])
    devices = [f"dev-{number:02d}11", f"dev-{number:02d}12"]
    docs = []
    travelled = 0.0
    speed_kph = rng.uniform(25, 40)
    ts = start
    while ts <= end:
        # Slow down at "corners" now and then, stop briefly once in a while.
        roll = rng.random()
        if roll < 0.08:
            speed_kph = 0.0
        elif roll < 0.30:
            speed_kph = rng.uniform(8, 20)
        else:
            speed_kph = min(50.0, max(15.0, speed_kph + rng.uniform(-6, 6)))
        travelled += speed_kph / 3.6 * interval_s
        lat, lon = point_along_loop(path, offset + travelled)
        docs.append({
            "ts": ts,
            "meta": {"tenantId": tenant_code, "vehicleId": code},
            "speedKph": round(speed_kph, 1),
            "driveMode": "AUTONOMOUS" if number != 1 else ("MANUAL" if rng.random() < 0.2 else "AUTONOMOUS"),
            "ignition": True,
            "location": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            "network": {"type": "CELLULAR", "rssiDbm": rng.randint(-95, -70)} if number != 1 else
                       {"type": "WIFI", "ssid": "Nguyen-Home", "rssiDbm": rng.randint(-70, -50)},
            "agent": {"version": "0.4.1", "uptimeS": 3600 + int((ts - start).total_seconds()),
                      "cpuPct": rng.randint(8, 25), "outboxDepth": 0},
            "devices": [{"deviceId": d, "ok": True, "muted": False, "levelDb": round(rng.uniform(35, 62), 1)}
                        for d in devices],
        })
        ts += timedelta(seconds=interval_s)
    return docs


# -------------------------------------------------------------------------------------------
# MySQL
# -------------------------------------------------------------------------------------------
async def seed_reference(session, hasher) -> None:
    for code, name, description, available in SERVICES:
        if await session.get(IntelligenceService, code) is None:
            session.add(IntelligenceService(service_code=code, name=name, description=description, available=available))
    for code, name, severity, minutes, description in ALERT_TYPES:
        if await session.get(AlertType, code) is None:
            session.add(AlertType(code=code, name=name, default_severity=severity, escalation_minutes=minutes,
                                  description=description))
    await session.flush()
    for t in TENANTS:
        if await session.get(Tenant, t["tenant_id"]) is None:
            session.add(Tenant(**t, time_zone="America/Los_Angeles", status="ACTIVE"))
    await session.flush()
    for u in USERS:
        if await session.get(AppUser, u["user_id"]) is None:
            session.add(AppUser(**u, password_hash=hasher.hash(DEMO_PASSWORD), status="ACTIVE",
                                must_change_password=False))
    await session.flush()


async def seed_vehicles(session) -> None:
    now = utcnow()
    for v in VEHICLES:
        if await session.get(Vehicle, v["vehicle_id"]) is None:
            fields = {k: val for k, val in v.items() if k != "online"}
            number = v["vehicle_code"].split("-")[1]
            session.add(Vehicle(**fields, retention_days=90, iot_thing_name=f"{TENANT_CODE[v['tenant_id']]}-{v['vehicle_code']}",
                                credential_version=1,
                                provisioned_at=None if v["status"] == "REGISTERED" else now - timedelta(days=30 + int(number)),
                                created_at=now - timedelta(days=45)))
    await session.flush()
    exists = (await session.execute(select(VehicleRider).where(VehicleRider.vehicle_id == 1,
                                                               VehicleRider.user_id == 1003))).scalars().first()
    if exists is None:
        session.add(VehicleRider(vehicle_id=1, user_id=1003, valid_from=now - timedelta(days=10), valid_to=None, tenant_id=1))
    await session.flush()


async def seed_subscriptions(session) -> None:
    for v in VEHICLES:
        rows = {r.service_code: r for r in (await session.execute(
            select(Subscription).where(Subscription.vehicle_id == v["vehicle_id"]))).scalars()}
        is_av12 = v["vehicle_code"] == "AV-12"
        for code in ("EMERGENCY", "ACCIDENT", "SECURITY", "SAFETY"):
            if code in rows:
                continue
            row = Subscription(tenant_id=v["tenant_id"], vehicle_id=v["vehicle_id"], service_code=code, enabled=True,
                               channels="DASHBOARD,EMAIL", notify_riders=is_av12, updated_by=TENANT_USER[v["tenant_id"]])
            if is_av12 and code == "SAFETY":
                row.enabled = False
            if is_av12 and code == "SECURITY":
                row.quiet_from, row.quiet_to = time(22, 0), time(6, 0)
            session.add(row)
    await session.flush()


def _alert_specs(now: datetime) -> List[Dict[str, Any]]:
    """The fixed alert 20482 plus 40 deterministic alerts over the last 14 days."""
    rng = random.Random(281)
    specs = [dict(alert_id=20482, tenant_id=1, vehicle="AV-01", cls="crash", state="MOVING", confidence=0.91,
                  status="NEW", created_at=datetime(2026, 11, 4, 21, 42, 12), lat=37.3352, lon=-121.8811,
                  event_ref="6729b1c4e1f0a2d3c4b5a697", channel="exterior")]
    class_weights = [("siren", 3), ("crash", 1), ("glass_break", 2), ("scream", 2), ("honking", 4)]
    pool = [c for c, w in class_weights for _ in range(w)]
    for i in range(40):
        tenant_id = 1 if rng.random() < 0.3 else 2
        vehicle = "AV-01" if tenant_id == 1 else rng.choice(["AV-10", "AV-11", "AV-12", "AV-13"])
        cls = rng.choice(pool)
        state = "MOVING" if cls == "crash" else rng.choice(["MOVING", "PARKED"])
        status = rng.choices(["NEW", "ACKNOWLEDGED", "RESOLVED", "FALSE_ALARM"], weights=[15, 20, 45, 20])[0]
        created = now - timedelta(minutes=rng.uniform(60, 14 * 24 * 60))
        if status == "NEW" and now - created > timedelta(days=2):
            status = "ACKNOWLEDGED"  # old alerts do not stay NEW
        lat = SJSU[0] + rng.uniform(-0.012, 0.012)
        lon = SJSU[1] + rng.uniform(-0.016, 0.016)
        specs.append(dict(alert_id=20441 + i, tenant_id=tenant_id, vehicle=vehicle, cls=cls, state=state,
                          confidence=round(rng.uniform(0.62, 0.97), 3), status=status, created_at=created,
                          lat=round(lat, 5), lon=round(lon, 5), event_ref=str(oid(f"alert-event-{i}")),
                          channel="interior" if cls == "scream" else "exterior",
                          ack_minutes=rng.randint(1, 40), close_minutes=rng.randint(45, 600)))
    return specs


async def seed_alerts(session, specs: List[Dict[str, Any]]) -> None:
    severities = {code: sev for code, _, sev, _, _ in ALERT_TYPES}
    for s in specs:
        existing = await session.get(Alert, s["alert_id"])
        if existing is not None:
            # Keep the audio event of a re-run consistent with the stored row.
            s["created_at"], s["status"] = existing.created_at, existing.status
            continue
        rule_id, alert_type = RULES[(s["cls"], s["state"])]
        actor = TENANT_USER[s["tenant_id"]]
        created = s["created_at"]
        ack_at = created + timedelta(minutes=s.get("ack_minutes", 5)) if s["status"] != "NEW" else None
        closed_at = (created + timedelta(minutes=s.get("close_minutes", 60))
                     if s["status"] in ("RESOLVED", "FALSE_ALARM") else None)
        alert = Alert(alert_id=s["alert_id"], tenant_id=s["tenant_id"], vehicle_id=s["vehicle"],
                      alert_type_code=alert_type, rule_id=rule_id, top_class_id=sound_class_id(s["cls"]),
                      severity=severities[alert_type], status=s["status"], event_ref=s["event_ref"],
                      confidence=s["confidence"], vehicle_state=s["state"], model_version=MODEL_VERSION_TAG, count=1,
                      lat=s["lat"], lon=s["lon"], created_at=created, updated_at=closed_at or ack_at or created,
                      acknowledged_by=actor if ack_at else None, acknowledged_at=ack_at,
                      closed_by=actor if closed_at else None, closed_at=closed_at)
        session.add(alert)
        session.add(AlertAction(alert_id=s["alert_id"], user_id=None, action="CREATE", from_status=None,
                                to_status="NEW", ts=created))
        if ack_at:
            session.add(AlertAction(alert_id=s["alert_id"], user_id=actor, action="ACKNOWLEDGE", from_status="NEW",
                                    to_status="ACKNOWLEDGED", ts=ack_at))
        if closed_at:
            session.add(AlertAction(alert_id=s["alert_id"], user_id=actor,
                                    action="RESOLVE" if s["status"] == "RESOLVED" else "FALSE_ALARM",
                                    from_status="ACKNOWLEDGED", to_status=s["status"],
                                    note="Confirmed on site" if s["status"] == "RESOLVED" else "Construction noise",
                                    ts=closed_at))
    await session.flush()


async def seed_models(session, storage) -> None:
    model = await session.get(AiModel, 1)
    if model is None:
        model = AiModel(model_id=1, name="sound-classifier", task="audio event classification (CLAP + head)",
                        created_by=1006, created_at=utcnow() - timedelta(days=60))
        session.add(model)
        await session.flush()
    now = utcnow()
    test = EVALUATION["split"]["test"]
    versions = [
        ("1.1.0", "ACTIVE", now - timedelta(days=40), now - timedelta(days=20),
         f"CLAP zero-shot with text prompts (no training); scored on {test} held-out course clips"),
        ("1.2.0", "REGISTERED", now - timedelta(days=5), None,
         f"CLAP + head trained on {EVALUATION['split']['train']} course clips; scored on the same {test} held-out clips"),
    ]
    bucket_ok: Optional[bool] = None
    for version, status, created, activated, notes in versions:
        scores = EVALUATION[version]
        data = ARTIFACTS[version]
        sha = hashlib.sha256(data).hexdigest()
        key = f"models/1/{version}/model.pt"
        existing = (await session.execute(select(ModelVersion).where(
            ModelVersion.model_id == 1, ModelVersion.version == version))).scalar_one_or_none()
        if existing is None:
            session.add(ModelVersion(model_id=1, version=version, status=status,
                                     artifact_uri=(storage.artifact_uri(1, version) if storage is not None
                                                   else f"local://scicp-models/{key}"),
                                     sha256=sha, size_bytes=len(data),
                                     metrics_json={"macroF1": scores["macroF1"], "accuracy": scores["accuracy"],
                                                   "perClass": scores["perClass"]},
                                     notes=notes, created_by=1006, created_at=created, activated_at=activated))
        if storage is not None:
            if bucket_ok is None:
                bucket_ok = await storage.ensure_bucket()
            if bucket_ok:
                try:
                    if await storage.head(key) is None:
                        await storage.put_bytes(key, data)
                        log.info("Uploaded artifact %s (%d bytes)", key, len(data))
                except Exception as exc:  # noqa: BLE001
                    log.warning("Artifact upload skipped for %s: %s", key, exc)
            else:
                log.info("Storage not reachable: artifact %s not uploaded (sha256 and size still recorded)", key)
    await session.flush()


async def seed_feedback(session, specs: List[Dict[str, Any]]) -> None:
    version = (await session.execute(select(ModelVersion).where(ModelVersion.model_id == 1,
                                                                ModelVersion.version == "1.1.0"))).scalar_one()
    rng = random.Random(7)
    candidates = [s for s in specs if s["status"] in ("RESOLVED", "FALSE_ALARM")][:15]
    for s in candidates:
        # Draw every random value before the existence check so that a re-run draws the same ones.
        user_id = TENANT_USER[s["tenant_id"]] if rng.random() < 0.7 else 1005
        if s["status"] == "FALSE_ALARM":
            label, corrected = ("WRONG_CLASS", rng.choice(["traffic", "environmental"])) if rng.random() < 0.3 else ("FALSE_ALARM", None)
        else:
            label, corrected = "CONFIRMED", None
        ts = s["created_at"] + timedelta(hours=rng.uniform(1, 30))
        exists = (await session.execute(select(FeedbackLabel).where(
            FeedbackLabel.event_ref == s["event_ref"], FeedbackLabel.user_id == user_id))).scalar_one_or_none()
        if exists:
            continue
        session.add(FeedbackLabel(event_ref=s["event_ref"], version_id=version.version_id, user_id=user_id, label=label,
                                  corrected_class=corrected, ts=ts))
    await session.flush()


# -------------------------------------------------------------------------------------------
# MongoDB
# -------------------------------------------------------------------------------------------
def _event_doc(doc_id: ObjectId, tenant_code: str, vehicle: str, ts: datetime, channel: str, cls: str, conf: float,
               lat: float, lon: float, state: str, rng: random.Random, decision: Dict[str, Any]) -> Dict[str, Any]:
    number = int(vehicle.split("-")[1])
    device = f"dev-{number:02d}{'11' if channel == 'interior' else '12'}"
    others = [c for c in CLASSES if c != cls]
    rng.shuffle(others)
    second = round(min(1 - conf, rng.uniform(0.02, 0.15)), 3)
    third = round(max(0.01, min(1 - conf - second, rng.uniform(0.01, 0.05))), 3)
    clip_id = hashlib.md5(str(doc_id).encode()).hexdigest()[:8]
    speed = round(rng.uniform(20, 55), 1) if state == "MOVING" else 0
    return {
        "_id": doc_id,
        "eventId": str(uuid.uuid5(uuid.NAMESPACE_URL, f"scicp/{tenant_code}/{vehicle}/{doc_id}")),
        "tenantId": tenant_code, "vehicleId": vehicle, "deviceId": device, "ts": ts, "channel": channel,
        "location": {"type": "Point", "coordinates": [lon, lat]},
        "clip": {"clipId": clip_id, "s3Key": f"tenants/{tenant_code}/vehicles/{vehicle}/{ts:%Y/%m/%d}/{clip_id}.wav",
                 "durationMs": 5000, "sampleRate": 16000},
        "edge": {"class": cls if rng.random() < 0.8 else others[0], "score": round(rng.uniform(0.4, 0.8), 2),
                 "priority": "HIGH" if cls in ("crash", "siren", "scream", "glass_break") else "NORMAL"},
        "inference": {"topK": [{"class": cls, "conf": conf}, {"class": others[0], "conf": second},
                               {"class": others[1], "conf": third}],
                      "modelVersion": MODEL_VERSION_TAG, "latencyMs": rng.randint(900, 1900)},
        "decision": decision,
        "context": {"speedKph": speed, "driveMode": "AUTONOMOUS", "ignition": state == "MOVING" or rng.random() < 0.5},
        "expireAt": ts + timedelta(days=90),
    }


async def seed_audio_events(mongo_db, specs: List[Dict[str, Any]], now: datetime) -> int:
    col = mongo_db["audio_events"]
    rng = random.Random(1234)
    severities = {code: sev for code, _, sev, _, _ in ALERT_TYPES}
    docs = []
    for s in specs:  # one event per alert, 5 s before the alert row
        rule_id, alert_type = RULES[(s["cls"], s["state"])]
        docs.append(_event_doc(ObjectId(s["event_ref"]), TENANT_CODE[s["tenant_id"]], s["vehicle"],
                               s["created_at"] - timedelta(seconds=5), s["channel"], s["cls"], s["confidence"],
                               s["lat"], s["lon"], s["state"], rng,
                               {"alert": True, "alertId": s["alert_id"], "alertType": alert_type,
                                "severity": severities[alert_type], "reason": "ALERT_CREATED"}))
    background = [("traffic", 50), ("environmental", 30), ("honking", 10), ("siren", 4), ("scream", 2),
                  ("glass_break", 2), ("crash", 1)]
    pool = [c for c, w in background for _ in range(w)]
    for i in range(360):
        tenant_id = 1 if rng.random() < 0.25 else 2
        vehicle = "AV-01" if tenant_id == 1 else rng.choice(["AV-10", "AV-11", "AV-12", "AV-13"])
        cls = rng.choice(pool)
        state = rng.choice(["MOVING", "MOVING", "PARKED"])
        ts = now - timedelta(minutes=rng.uniform(5, 14 * 24 * 60))
        if cls in ("traffic", "environmental"):
            conf, reason = round(rng.uniform(0.55, 0.97), 3), "NO_RULE"
        elif cls == "honking":
            conf, reason = round(rng.uniform(0.6, 0.9), 3), "REPEAT_PENDING"
        else:
            conf, reason = round(rng.uniform(0.3, 0.58), 3), "BELOW_THRESHOLD"
        lat = SJSU[0] + rng.uniform(-0.012, 0.012)
        lon = SJSU[1] + rng.uniform(-0.016, 0.016)
        docs.append(_event_doc(oid(f"background-event-{i}"), TENANT_CODE[tenant_id], vehicle, ts,
                               "interior" if cls == "scream" else "exterior", cls, conf, round(lat, 5), round(lon, 5),
                               state, rng, {"alert": False, "reason": reason}))
    for doc in docs:
        await col.replace_one({"_id": doc["_id"]}, doc, upsert=True)
    try:
        await col.create_index([("tenantId", 1), ("vehicleId", 1), ("ts", 1)])
        await col.create_index([("tenantId", 1), ("eventId", 1)], unique=True)
        await col.create_index([("tenantId", 1), ("decision.alert", 1), ("ts", 1)])
    except Exception as exc:  # noqa: BLE001 - index creation is best effort on the mock
        log.debug("audio_events indexes skipped: %s", exc)
    return len(docs)


async def seed_telemetry(mongo_db, now: datetime) -> int:
    col = mongo_db["vehicle_telemetry"]
    rng = random.Random(99)
    count = 0
    for v in VEHICLES:
        code = v["vehicle_code"]
        await col.delete_many({"meta.vehicleId": code, "meta.tenantId": TENANT_CODE[v["tenant_id"]]})
        if v["online"] is None:
            continue  # AV-14 has never sent telemetry
        if v["online"]:
            start, end = now - timedelta(minutes=10), now
        else:
            start, end = now - timedelta(minutes=24), now - timedelta(minutes=14)  # AV-13 went silent
        docs = telemetry_samples(code, TENANT_CODE[v["tenant_id"]], start, end, rng)
        if docs:
            await col.insert_many(docs)
            count += len(docs)
    try:
        await col.create_index([("meta.tenantId", 1), ("meta.vehicleId", 1), ("ts", -1)])
    except Exception as exc:  # noqa: BLE001
        log.debug("vehicle_telemetry index skipped: %s", exc)
    return count


# -------------------------------------------------------------------------------------------
# Entry points
# -------------------------------------------------------------------------------------------
async def run(engine_or_url, mongo_db, storage=None) -> Dict[str, int]:
    """Seed MySQL (through the engine or URL) and the MongoDB database. Idempotent."""
    from app.core.security import PasswordHasher

    engine: AsyncEngine = engine_or_url if isinstance(engine_or_url, AsyncEngine) else make_engine(engine_or_url)
    own_engine = engine is not engine_or_url
    await create_schema(engine)
    now = utcnow()
    specs = _alert_specs(now)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed_reference(session, PasswordHasher())
        await seed_vehicles(session)
        await seed_subscriptions(session)
        await seed_alerts(session, specs)
        await seed_models(session, storage)
        await seed_feedback(session, specs)
        await session.commit()
        counts = {
            "alerts": int((await session.execute(select(func.count()).select_from(Alert))).scalar_one()),
            "users": int((await session.execute(select(func.count()).select_from(AppUser))).scalar_one()),
            "vehicles": int((await session.execute(select(func.count()).select_from(Vehicle))).scalar_one()),
        }
    counts["audioEvents"] = await seed_audio_events(mongo_db, specs, now)
    counts["telemetrySamples"] = await seed_telemetry(mongo_db, now)
    if own_engine:
        await engine.dispose()
    log.info("Seed done: %s", counts)
    return counts


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from app.config import Settings
    from app.clients import make_storage

    settings = Settings()
    mongo_db = make_mongo(settings.mongo_url, settings.mongo_database_name)
    # The same client the API uses (STORAGE_BACKEND=local: the shared "objects" volume;
    # s3: the scicp-models bucket through the instance role or the keys in .env).
    storage = make_storage(settings)
    counts = asyncio.run(run(settings.database_url, mongo_db, storage=storage))
    print(counts)


if __name__ == "__main__":
    main()
