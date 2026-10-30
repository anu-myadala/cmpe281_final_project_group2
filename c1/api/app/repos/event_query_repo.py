"""EventQueryRepo: MongoDB aggregation pipelines (Motor) on audio_events and
the newest vehicle_telemetry document per car. Every $match starts with the
tenant code so that the {tenantId, vehicleId, ts} index is used; C1 writes
nothing to MongoDB."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from bson import ObjectId
from bson.errors import InvalidId

AUDIO_EVENTS = "audio_events"
VEHICLE_TELEMETRY = "vehicle_telemetry"


class EventQueryRepo:
    def __init__(self, db: Any) -> None:
        self.db = db

    @property
    def events(self):
        return self.db[AUDIO_EVENTS]

    @property
    def telemetry(self):
        return self.db[VEHICLE_TELEMETRY]

    # -- audio_events --------------------------------------------------------------
    @staticmethod
    def _range(tenant_code: str, from_: datetime, to: datetime, vehicle_code: Optional[str] = None,
               vehicle_codes: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        match: Dict[str, Any] = {"tenantId": tenant_code, "ts": {"$gte": from_, "$lt": to}}
        if vehicle_code:
            match["vehicleId"] = vehicle_code
        elif vehicle_codes is not None:
            match["vehicleId"] = {"$in": list(vehicle_codes)}
        return match

    async def count_events(self, tenant_code: str, from_: datetime, to: datetime, vehicle_code=None,
                           vehicle_codes=None) -> int:
        return int(await self.events.count_documents(self._range(tenant_code, from_, to, vehicle_code, vehicle_codes)))

    async def events_by_class(self, tenant_code: str, from_: datetime, to: datetime, vehicle_code: Optional[str] = None,
                              per_vehicle: bool = False, vehicle_codes=None) -> List[Dict[str, Any]]:
        """[{class, vehicleId?, count, meanConf, lastEventAt}] sorted by count."""
        group_id: Any = {"cls": "$cls", "vehicleId": "$vehicleId"} if per_vehicle else "$cls"
        pipeline = [
            {"$match": self._range(tenant_code, from_, to, vehicle_code, vehicle_codes)},
            {"$project": {"vehicleId": 1, "ts": 1,
                          "cls": {"$arrayElemAt": ["$inference.topK.class", 0]},
                          "conf": {"$arrayElemAt": ["$inference.topK.conf", 0]}}},
            {"$group": {"_id": group_id, "count": {"$sum": 1}, "meanConf": {"$avg": "$conf"},
                        "lastEventAt": {"$max": "$ts"}}},
            {"$sort": {"count": -1}},
        ]
        out = []
        for doc in await self.events.aggregate(pipeline).to_list(None):
            key = doc["_id"]
            entry = {"class": key["cls"] if per_vehicle else key, "count": int(doc["count"]),
                     "meanConf": round(doc["meanConf"], 4) if doc.get("meanConf") is not None else None,
                     "lastEventAt": doc.get("lastEventAt")}
            if per_vehicle:
                entry["vehicleId"] = key["vehicleId"]
            if entry["class"] is None:
                entry["class"] = "unknown"
            out.append(entry)
        return out

    async def vehicle_activity(self, tenant_code: str, from_: datetime, to: datetime) -> Dict[str, Dict[str, Any]]:
        """Per vehicle: event count, last event time and the most frequent class."""
        rows = await self.events_by_class(tenant_code, from_, to, per_vehicle=True)
        out: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            entry = out.setdefault(r["vehicleId"], {"events": 0, "lastEventAt": None, "topClass": None, "_top": -1})
            entry["events"] += r["count"]
            if entry["lastEventAt"] is None or (r["lastEventAt"] and r["lastEventAt"] > entry["lastEventAt"]):
                entry["lastEventAt"] = r["lastEventAt"]
            if r["count"] > entry["_top"]:
                entry["_top"] = r["count"]
                entry["topClass"] = r["class"]
        for entry in out.values():
            entry.pop("_top", None)
        return out

    async def find_event(self, event_ref: str) -> Optional[Dict[str, Any]]:
        try:
            oid = ObjectId(event_ref)
        except (InvalidId, TypeError):
            return None
        return await self.events.find_one({"_id": oid})

    async def events_by_refs(self, refs: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        oids = []
        for ref in refs:
            try:
                oids.append(ObjectId(ref))
            except (InvalidId, TypeError):
                continue
        if not oids:
            return {}
        docs = await self.events.find({"_id": {"$in": oids}}).to_list(None)
        return {str(d["_id"]): d for d in docs}

    # -- vehicle_telemetry -----------------------------------------------------------
    async def latest_positions(self, tenant_code: str) -> Dict[str, Dict[str, Any]]:
        """{vehicleId: {latest: document, previous: {ts, location} | None}} in one pipeline:
        $match meta.tenantId, $sort ts descending, $group by meta.vehicleId with $first."""
        pipeline = [
            {"$match": {"meta.tenantId": tenant_code}},
            {"$sort": {"ts": -1}},
            {"$group": {"_id": "$meta.vehicleId", "latest": {"$first": "$$ROOT"},
                        "trail": {"$push": {"ts": "$ts", "location": "$location"}}}},
            {"$project": {"latest": 1, "previous": {"$arrayElemAt": ["$trail", 1]}}},
        ]
        out: Dict[str, Dict[str, Any]] = {}
        for doc in await self.telemetry.aggregate(pipeline).to_list(None):
            out[doc["_id"]] = {"latest": doc["latest"], "previous": doc.get("previous")}
        return out
