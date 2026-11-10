"""The audio_events document, built like c3/tools/send_result.py and check_c2.py, and its idempotent insert.

tenantId and vehicleId come from the MQTT topic. ts is stored as an aware datetime and
expireAt = ts + 90 days. The seed's unique index on (tenantId, eventId) makes a redelivered event a
DuplicateKeyError: the stored _id is reused and nothing is inserted again (the stored document only
gets an inference block if it has none).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

COLLECTION = "audio_events"
RETENTION = timedelta(days=90)


def parse_ts(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def build_doc(tenant_id: str, vehicle_id: str, ev: Dict[str, Any], inference: Dict[str, Any]) -> Dict[str, Any]:
    ts = parse_ts(ev.get("ts"))
    clip = ev.get("clip") if isinstance(ev.get("clip"), dict) else {}
    return {"_id": ObjectId(), "eventId": ev.get("eventId"), "tenantId": tenant_id, "vehicleId": vehicle_id,
            "deviceId": ev.get("deviceId"), "ts": ts, "channel": ev.get("channel"), "location": ev.get("location"),
            "clip": {k: clip.get(k) for k in ("clipId", "s3Key", "durationMs", "sampleRate")},
            "edge": ev.get("edge"), "inference": inference, "context": ev.get("context"),
            "expireAt": ts + RETENTION}


def inference_block(top_k, model_version: str, latency_ms: int, error: Optional[str] = None) -> Dict[str, Any]:
    block: Dict[str, Any] = {"topK": [{"class": c, "conf": p} for c, p in top_k], "modelVersion": model_version,
                             "latencyMs": latency_ms}
    if error:
        block["error"] = error
    return block


async def ensure_indexes(db) -> None:
    """Same spec as seed/seed.py, so it is a no-op on a seeded database."""
    await db[COLLECTION].create_index([("tenantId", 1), ("eventId", 1)], unique=True)


async def store_event(db, doc: Dict[str, Any]) -> Tuple[str, bool]:
    """Returns (eventRef, inserted). A duplicate (tenantId, eventId) returns the stored _id."""
    col = db[COLLECTION]
    key = {"tenantId": doc["tenantId"], "eventId": doc["eventId"]}
    existing = await col.find_one(key, {"_id": 1, "inference": 1})
    if existing is None:
        try:
            await col.insert_one(doc)
            return str(doc["_id"]), True
        except DuplicateKeyError:
            existing = await col.find_one(key, {"_id": 1, "inference": 1})
            if existing is None:
                raise
    if not existing.get("inference"):
        await col.update_one({"_id": existing["_id"]}, {"$set": {"inference": doc["inference"]}})
    return str(existing["_id"]), False
