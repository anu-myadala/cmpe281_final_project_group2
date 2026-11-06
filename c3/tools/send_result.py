"""Demo tool: play the classifier worker for one sound and let C3 decide.

It does what the C4 worker does after classifying a clip: it stores an audio event in
MongoDB (shaped like Part III, Table 96, at the car's newest telemetry position) and calls
C3's POST /internal/events/{eventRef}/result with a service token. It prints the decision,
and for an alert the link to the alert page.

    # inside the c3 container (it has MONGO_URL, SERVICE_TOKEN_SECRET and PUBLIC_BASE_URL):
    docker compose -f docker-compose.yml -f docker-compose.c3.yml exec c3 \\
        python tools/send_result.py crash 0.91 AV-01 tA 48
    #   class conf vehicle tenant speedKph       -> ACCIDENT / CRITICAL for Anh
    python tools/send_result.py scream 0.83 AV-12 tB 0                  # SECURITY / HIGH for Maria
    python tools/send_result.py honking 0.80 AV-10 tB 30 --repeat 2     # REPEAT_PENDING, then SAFETY
    python tools/send_result.py honking 0.80 AV-12 tB 30                # SERVICE_OFF (SAFETY is off on AV-12)
    python tools/send_result.py traffic 0.95 AV-01 tA 30                # NO_RULE
    python tools/send_result.py crash 0.91 AV-01 tA 48 --also glass_break:0.06 --also traffic:0.02

From the host instead: MONGO_URL=mongodb://localhost:27017/scicp C3_URL=http://localhost:8003/api/v1
SERVICE_TOKEN_SECRET=<from .env> c3/.venv/bin/python c3/tools/send_result.py ...
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import jwt
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

SJSU = (37.3352, -121.8811)


def service_token(secret: str) -> str:
    now = int(time.time())
    return jwt.encode({"sub": "c4-tool", "role": "SERVICE", "iat": now, "exp": now + 600, "jti": uuid.uuid4().hex},
                      secret, algorithm="HS256")


def database_name(mongo_url: str) -> str:
    path = mongo_url.split("//", 1)[-1].split("/", 1)
    return (path[1].split("?", 1)[0] if len(path) == 2 else "") or "scicp"


async def car_position(db, tenant: str, vehicle: str) -> tuple:
    """(lat, lon) of the car's newest telemetry sample, so the map pin appears where the car is."""
    doc = await db["vehicle_telemetry"].find_one({"meta.tenantId": tenant, "meta.vehicleId": vehicle},
                                                 sort=[("ts", -1)])
    coords = ((doc or {}).get("location") or {}).get("coordinates")
    return (coords[1], coords[0]) if coords else SJSU


async def send_one(db, client: httpx.AsyncClient, args, top_k: list, model_version: str) -> dict:
    lat, lon = await car_position(db, args.tenant, args.vehicle)
    ts = datetime.now(timezone.utc)
    number = int(args.vehicle.split("-")[-1])
    channel = "interior" if args.cls == "scream" else "exterior"
    clip_id = uuid.uuid4().hex[:8]
    doc = {"_id": ObjectId(), "eventId": str(uuid.uuid4()), "tenantId": args.tenant, "vehicleId": args.vehicle,
           "deviceId": f"dev-{number:02d}{'11' if channel == 'interior' else '12'}", "ts": ts, "channel": channel,
           "location": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
           "clip": {"clipId": clip_id, "s3Key": f"tenants/{args.tenant}/vehicles/{args.vehicle}/{ts:%Y/%m/%d}/{clip_id}.wav",
                    "durationMs": 5000, "sampleRate": 16000},
           "edge": {"class": args.cls, "score": round(min(0.99, args.conf * 0.8), 2),
                    "priority": "HIGH" if args.cls in ("crash", "siren", "scream", "glass_break") else "NORMAL"},
           "inference": {"topK": [{"class": c, "conf": p} for c, p in top_k], "modelVersion": model_version,
                         "latencyMs": 1480},
           "context": {"speedKph": args.speed, "driveMode": "AUTONOMOUS", "ignition": args.ignition},
           "expireAt": ts + timedelta(days=90)}
    await db["audio_events"].insert_one(doc)
    body = {"tenantId": args.tenant, "vehicleId": args.vehicle, "topK": [{"class": c, "conf": p} for c, p in top_k],
            "modelVersion": model_version, "latencyMs": 1480}
    if args.error:
        body["error"] = args.error
    response = await client.post(f"{args.c3_url.rstrip('/')}/internal/events/{doc['_id']}/result", json=body,
                                 headers={"Authorization": f"Bearer {service_token(args.secret)}"})
    return {"eventRef": str(doc["_id"]), "status": response.status_code, "body": response.json()}


async def main() -> int:
    parser = argparse.ArgumentParser(description="Store an audio event and post its classifier result to C3.")
    parser.add_argument("cls", help="top class: siren crash glass_break scream honking traffic environmental")
    parser.add_argument("conf", type=float, help="its confidence, 0..1")
    parser.add_argument("vehicle", help="vehicle code, e.g. AV-01")
    parser.add_argument("tenant", help="tenant code, e.g. tA")
    parser.add_argument("speed", type=float, help="speedKph (over 5 with ignition on = MOVING)")
    parser.add_argument("--also", action="append", default=[], metavar="CLASS:CONF", help="2nd / 3rd class of the top 3")
    parser.add_argument("--ignition", choices=["on", "off"], help="default: on when speed > 0")
    parser.add_argument("--repeat", type=int, default=1, help="send N such events (honking needs 2 within 10 s)")
    parser.add_argument("--gap", type=float, default=3.0, help="seconds between repeated events")
    parser.add_argument("--error", help="a worker error (clip not fetched): gives ERROR_RECORDED")
    parser.add_argument("--model-version", default=os.environ.get("MODEL_VERSION", "sound-classifier-1.1.0"))
    parser.add_argument("--c3-url", default=os.environ.get("C3_URL", "http://localhost:8003/api/v1"))
    args = parser.parse_args()
    args.ignition = (args.ignition == "on") if args.ignition else args.speed > 0
    args.secret = os.environ.get("SERVICE_TOKEN_SECRET")
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017/scicp")
    if not args.secret:
        print("SERVICE_TOKEN_SECRET is not set (run it inside the c3 container, or export it from .env)", file=sys.stderr)
        return 2
    top_k = [(args.cls, args.conf)] + [(a.split(":")[0], float(a.split(":")[1])) for a in args.also]
    db = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)[database_name(mongo_url)]
    base = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")
    async with httpx.AsyncClient(timeout=10) as client:
        for i in range(args.repeat):
            if i:
                await asyncio.sleep(args.gap)
            result = await send_one(db, client, args, top_k, args.model_version)
            decision = (result["body"] or {}).get("decision", result["body"])
            print(f"event {result['eventRef']} -> HTTP {result['status']} {json.dumps(decision)}")
            if isinstance(decision, dict) and decision.get("alertId") and decision.get("reason") == "ALERT_CREATED":
                print(f"   alert page: {base}/alerts/{decision['alertId']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
