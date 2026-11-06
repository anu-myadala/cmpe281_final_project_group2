"""Helpers for the result-call tests: a C3 app on a fresh copy of the seeded database with
its own empty MongoDB mock, a recording publisher, and an audio-event builder that writes
documents shaped like the worker's (Part III, Table 96)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest_asyncio
from bson import ObjectId
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient
from sqlalchemy import select

from c3_service.main import create_app
from c3_service.models import Alert
from c3_service.timeutil import utcnow
from conftest import bearer, make_settings, service_token, sqlite_url

AV01 = (37.3352, -121.8811)


class RecordingPublisher:
    """Records alert.created with the alert row as a separate session sees it at that moment (None = not committed)."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.session_factory = None
        self.calls: List[Tuple[str, Dict[str, Any], int, Optional[str]]] = []

    async def publish(self, topic: str, payload: Dict[str, Any], tenant_id: int) -> None:
        alert_id = (payload.get("alert") or {}).get("alertId", payload.get("alertId"))
        async with self.session_factory() as session:
            committed = (await session.execute(select(Alert.status).where(Alert.alert_id == alert_id))).scalar_one_or_none()
        self.calls.append((topic, payload, tenant_id, committed))
        if self.fail:
            raise RuntimeError("event bus down")


@dataclass
class EngineEnv:
    client: AsyncClient
    app: Any
    mongo: Any
    publisher: RecordingPublisher
    now: datetime = field(default_factory=utcnow)

    async def add_event(self, tenant: str = "tA", vehicle: str = "AV-01", top_k: Sequence[Tuple[str, float]] = (("crash", 0.91),),
                        speed: float = 48, ignition: bool = True, at: Optional[datetime] = None,
                        position: Tuple[float, float] = AV01, decision: Optional[Dict[str, Any]] = None) -> str:
        """Stores an audio event as the worker would (before its result call) and returns its eventRef."""
        ts = at or self.now
        number = int(vehicle.split("-")[1])
        doc = {"_id": ObjectId(), "eventId": str(uuid.uuid4()), "tenantId": tenant, "vehicleId": vehicle,
               "deviceId": f"dev-{number:02d}12", "ts": ts, "channel": "exterior",
               "location": {"type": "Point", "coordinates": [position[1], position[0]]},
               "clip": {"clipId": uuid.uuid4().hex[:8], "s3Key": f"tenants/{tenant}/vehicles/{vehicle}/x.wav",
                        "durationMs": 5000, "sampleRate": 16000},
               "edge": {"class": top_k[0][0], "score": 0.7, "priority": "HIGH"},
               "inference": {"topK": [{"class": c, "conf": p} for c, p in top_k], "modelVersion": "sound-classifier-1.1.0",
                             "latencyMs": 1480},
               "context": {"speedKph": speed, "driveMode": "AUTONOMOUS", "ignition": ignition},
               "expireAt": ts + timedelta(days=90)}
        if decision is not None:
            doc["decision"] = decision
        await self.mongo["audio_events"].insert_one(doc)
        return str(doc["_id"])

    async def post(self, ref: str, tenant: str = "tA", vehicle: str = "AV-01",
                   top_k: Optional[Sequence[Tuple[str, float]]] = None, error: Optional[str] = None,
                   headers: Optional[Dict[str, str]] = None, model_version: str = "sound-classifier-1.1.0"):
        """The worker's result call; top_k defaults to what the stored event says."""
        if top_k is None:
            doc = await self.mongo["audio_events"].find_one({"_id": ObjectId(ref)}) if ObjectId.is_valid(ref) else None
            top_k = [(s["class"], s["conf"]) for s in (doc or {}).get("inference", {}).get("topK", [])] or [("crash", 0.9)]
        body = {"tenantId": tenant, "vehicleId": vehicle, "topK": [{"class": c, "conf": p} for c, p in top_k],
                "modelVersion": model_version, "latencyMs": 1480}
        if error is not None:
            body["error"] = error
        return await self.client.post(f"/api/v1/internal/events/{ref}/result", json=body,
                                      headers=bearer(service_token()) if headers is None else headers)

    async def alerts_for(self, ref: str) -> List[Alert]:
        async with self.app.state.session_factory() as session:
            return list((await session.execute(select(Alert).where(Alert.event_ref == ref))).scalars().all())

    async def stored_decision(self, ref: str) -> Optional[Dict[str, Any]]:
        return (await self.mongo["audio_events"].find_one({"_id": ObjectId(ref)}) or {}).get("decision")


@pytest_asyncio.fixture
async def engine_env(fresh_db_copy):
    """A C3 app on a fresh copy of the seeded database, an empty MongoDB, a recording publisher."""
    stack = []

    async def build(publisher_fails: bool = False) -> EngineEnv:
        mongo, publisher = AsyncMongoMockClient()["scicp"], RecordingPublisher(fail=publisher_fails)
        app = create_app(make_settings(database_url=sqlite_url(fresh_db_copy(f"engine{len(stack)}.db"))), mongo=mongo,
                         publisher=publisher)
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        publisher.session_factory = app.state.session_factory
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://c3.test")
        stack.append((client, lifespan))
        return EngineEnv(client, app, mongo, publisher)

    yield build
    for client, lifespan in reversed(stack):
        await client.aclose()
        await lifespan.__aexit__(None, None, None)
