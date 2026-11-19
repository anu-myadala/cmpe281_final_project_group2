"""HealthService (M8, Platform health page): probes every dependency of the
platform from the API and reports one row per part with its state and the time
it took. A probe that fails never raises; it reports DOWN with the reason, so
the page shows a degraded platform instead of an error."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..container import AppContainer
from ..core.timeutil import iso, parse_iso, utcnow
from ..errors import Validation
from .deployment_service import STALE_AFTER

PROBE_TIMEOUT = 3.0


async def _timed(probe: Callable[[], Awaitable[Optional[str]]]) -> Dict[str, Any]:
    """Run one probe: UP with the latency, or DOWN with the reason."""
    started = time.perf_counter()
    try:
        detail = await asyncio.wait_for(probe(), PROBE_TIMEOUT)
        return {"state": "UP", "latencyMs": round((time.perf_counter() - started) * 1000, 1), "detail": detail}
    except asyncio.TimeoutError:
        return {"state": "DOWN", "latencyMs": None, "detail": f"no answer within {PROBE_TIMEOUT:.0f} s"}
    except Exception as exc:  # noqa: BLE001 - any failure is a DOWN row
        return {"state": "DOWN", "latencyMs": None, "detail": str(exc) or exc.__class__.__name__}


class HealthService:
    def __init__(self, container: AppContainer, session: AsyncSession) -> None:
        self.c = container
        self.session = session

    async def _mysql(self) -> Optional[str]:
        await self.session.execute(text("SELECT 1"))
        return self.c.engine.dialect.name

    async def _mongo(self) -> Optional[str]:
        if self.c.settings.mongo_url == "mock":
            await self.c.mongo["audio_events"].find_one({})
            return "in-memory mock"
        await self.c.mongo.command("ping")
        return self.c.settings.mongo_database_name

    async def _mqtt(self) -> Optional[str]:
        url = self.c.settings.mqtt_url
        if not url:
            raise RuntimeError("MQTT_URL is not set")
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (8883 if parsed.scheme in ("mqtts", "ssl") else 1883)
        _, writer = await asyncio.open_connection(host, port)
        writer.close()
        return f"{host}:{port}"

    async def _service(self, client: Any) -> Optional[str]:
        body = await client.health()
        state = body.get("status") if isinstance(body, dict) else None
        database = body.get("database") if isinstance(body, dict) else None
        if state not in (None, "ok") or database not in (None, "ok"):
            raise RuntimeError(f"status {state}, database {database}")
        return client.base_url

    def _workers(self) -> List[Dict[str, Any]]:
        now = utcnow()
        rows = []
        for entry in sorted(self.c.workers.values(), key=lambda e: e.get("workerId", "")):
            try:
                beat = parse_iso(entry["lastHeartbeatAt"]) if entry.get("lastHeartbeatAt") else None
            except Validation:
                beat = None
            stale = beat is None or now - beat > STALE_AFTER
            healthy = entry.get("state") == "READY" and not stale
            rows.append({"name": entry.get("workerId"), "kind": "Classifier worker (C4)",
                         "state": "UP" if healthy else ("STALE" if entry.get("state") == "READY" else "DOWN"),
                         "latencyMs": None,
                         "detail": f"{entry.get('modelVersion') or '-'} {entry.get('state') or ''}".strip(),
                         "lastSeenAt": entry.get("lastHeartbeatAt")})
        return rows

    async def report(self) -> Dict[str, Any]:
        probes = [
            ("api", "C1 API (this process)", self._noop),
            ("mysql", "MySQL (shared SQL database)", self._mysql),
            ("mongodb", "MongoDB (audio events, telemetry)", self._mongo),
            ("mqtt", "MQTT broker (model activation)", self._mqtt),
            ("c2", "C2 vehicle and device service", lambda: self._service(self.c.vehicle_client)),
            ("c3", "C3 alert service", lambda: self._service(self.c.alert_client)),
        ]
        # The local probes run one after the other so their latency is their own; the network probes
        # run together so that one neighbor that is down costs at most PROBE_TIMEOUT.
        results = [await _timed(p) for _, _, p in probes[:3]]
        results += await asyncio.gather(*(_timed(p) for _, _, p in probes[3:]))
        parts = [{"name": name, "kind": kind, **result} for (name, kind, _), result in zip(probes, results)]
        parts.extend(self._workers())
        down = [p["name"] for p in parts if p["state"] != "UP"]
        return {"status": "UP" if not down else "DEGRADED", "down": down, "parts": parts,
                "uptimeSeconds": round(time.monotonic() - self.c.started_monotonic),
                "checkedAt": iso(utcnow())}

    @staticmethod
    async def _noop() -> Optional[str]:
        return "answering"
