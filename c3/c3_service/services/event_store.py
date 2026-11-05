"""MongoDB reads of C3: the audio event of an alert and the newest car position.

This is the EventRepo of Part III, Table 88. Every filter starts with the tenant code
(objective O6, Section 3.3.2: "every MongoDB pipeline with tenantId"), so a wrong
event_ref can never show another tenant's event. eventRef is the 24-hex ObjectId of
the audio_events document (alert.event_ref).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Tuple

from bson import ObjectId
from bson.errors import InvalidId


def _object_id(event_ref: Optional[str]) -> Optional[ObjectId]:
    try:
        return ObjectId(event_ref)
    except (InvalidId, TypeError):
        return None


class EventStore:
    def __init__(self, mongo: Any) -> None:
        self.mongo = mongo

    async def get(self, event_ref: Optional[str], tenant_code: Optional[str]) -> Optional[Dict[str, Any]]:
        """The audio_events document of one alert, or None (unknown, expired by TTL, or malformed ref)."""
        oid = _object_id(event_ref)
        if oid is None or tenant_code is None:
            return None
        return await self.mongo["audio_events"].find_one({"tenantId": tenant_code, "_id": oid})

    async def many(self, event_refs: Iterable[Optional[str]], tenant_code: Optional[str]) -> Dict[str, Dict[str, Any]]:
        """{eventRef: document} for several alerts in one query."""
        oids = [oid for oid in (_object_id(r) for r in event_refs) if oid is not None]
        if not oids or tenant_code is None:
            return {}
        cursor = self.mongo["audio_events"].find({"tenantId": tenant_code, "_id": {"$in": oids}})
        return {str(doc["_id"]): doc for doc in await cursor.to_list(None)}

    async def latest_positions(self, tenant_code: Optional[str]) -> Dict[str, Tuple[float, float]]:
        """{vehicleCode: (lat, lon)} from each car's newest vehicle_telemetry sample (one aggregation)."""
        if tenant_code is None:
            return {}
        pipeline = [{"$match": {"meta.tenantId": tenant_code}}, {"$sort": {"ts": -1}},
                    {"$group": {"_id": "$meta.vehicleId", "latest": {"$first": "$$ROOT"}}}]
        out: Dict[str, Tuple[float, float]] = {}
        for doc in await self.mongo["vehicle_telemetry"].aggregate(pipeline).to_list(None):
            position = location_of(doc["latest"])
            if position is not None:
                out[doc["_id"]] = position
        return out


def location_of(doc: Optional[Dict[str, Any]]) -> Optional[Tuple[float, float]]:
    """(lat, lon) of a document with a GeoJSON Point `location` ([lon, lat]), or None."""
    coords = ((doc or {}).get("location") or {}).get("coordinates")
    if not coords or len(coords) < 2 or coords[0] is None or coords[1] is None:
        return None
    return coords[1], coords[0]
