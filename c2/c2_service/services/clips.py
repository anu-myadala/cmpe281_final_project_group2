"""M6 Clip service and M7 Retention (Sections 4.6 and 4.7 of the C2 design, FC1).

    ingest            POST /ingest/clips with the device token (BR-C2-14 to BR-C2-17)
    fetch             GET /internal/clips/{clipId} for the C4 worker (BR-C2-22)
    list_for_vehicle  the Data tab: the clip table joined with the class and alert of audio_events
    signed URLs       the browser plays a clip through an HMAC-signed C2 path, valid 15 minutes
    delete, run_retention, set_retention
"""
from __future__ import annotations

import hashlib
import hmac
import io
import logging
import secrets
import time
import wave
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from ..auth import Caller, decode_device_token
from ..errors import (BadGateway, BusinessRule, Gone, NotFound, PayloadTooLarge, RateLimit, Unauthorized,
                      UnsupportedMedia, Validation)
from ..models import MIC_TYPES, Clip, Device, Vehicle
from ..timeutil import as_naive_utc, iso, parse_iso, utcnow
from .support import audit

log = logging.getLogger("scicp.c2.clips")

RIFF_HEADER = 44


def wav_info(data: bytes) -> Dict[str, Any]:
    """Reads the RIFF header only. Raises UnsupportedMedia for anything but PCM WAV."""
    if len(data) < RIFF_HEADER or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise UnsupportedMedia("The clip must be a RIFF WAVE file", {"found": "not WAV"})
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    except (wave.Error, EOFError) as exc:
        raise UnsupportedMedia("The WAV header could not be read", {"found": str(exc)})
    return {"channels": channels, "bits": width * 8, "rate": rate, "durationMs": int(round(frames * 1000 / rate)) if rate else 0}


def clip_key(tenant_code: str, vehicle_code: str, captured: datetime, clip_id: str) -> str:
    return f"tenants/{tenant_code}/vehicles/{vehicle_code}/{captured:%Y/%m/%d}/{clip_id}.wav"


class ClipService:
    def __init__(self, session_factory: Any, mongo: Any, store: Any, tenants: Any, settings: Any) -> None:
        self.session_factory = session_factory
        self.mongo = mongo
        self.store = store
        self.tenants = tenants
        self.settings = settings

    # -- upload (FC1) -------------------------------------------------------------------------------------
    async def ingest(self, token: str, data: bytes, device_code: str, event_id: str, ts: Optional[str]) -> tuple:
        """Returns (http_status, body). Every check before any work on the file is cheap (FC1 order)."""
        claims = decode_device_token(token, self.settings)                       # U1
        now = utcnow()
        async with self.session_factory() as session:
            tenant_id = await self.tenants.id_of(str(claims.get("tenantId")))
            vehicle = None
            if tenant_id is not None:
                vehicle = (await session.execute(select(Vehicle).where(
                    Vehicle.tenant_id == tenant_id, Vehicle.vehicle_code == claims["vehicleId"]))).scalars().first()
            if vehicle is None or vehicle.status == "RETIRED" or claims.get("ver") != vehicle.credential_version:
                raise Unauthorized("The device token is no longer valid; provision the car again")
            device = (await session.execute(select(Device).where(
                Device.vehicle_id == vehicle.vehicle_id, Device.device_code == device_code,
                Device.deleted_at.is_(None)))).scalars().first()
            if device is None or device.device_type not in MIC_TYPES or device.status == "DISABLED" or device.muted:
                raise BusinessRule("BR-C2-15", "The device must be an enabled, unmuted microphone of this car",
                                   field="deviceId")                           # U2
            if not event_id or len(event_id) > 36:
                raise Validation("eventId is required (a UUID)", {"field": "eventId"})
            old = (await session.execute(select(Clip).where(Clip.vehicle_id == vehicle.vehicle_id,
                                                             Clip.event_id == event_id))).scalars().first()
            if old is not None:
                return 200, self._upload_body(old)                              # U3, BR-C2-16
            recent = (await session.execute(select(func.count()).select_from(Clip).where(
                Clip.vehicle_id == vehicle.vehicle_id, Clip.uploaded_at >= now - timedelta(seconds=60)))).scalar_one()
            if recent >= self.settings.c2_clips_per_minute:
                raise RateLimit(f"At most {self.settings.c2_clips_per_minute} clips per minute per car")  # U4
            if len(data) > self.settings.c2_max_clip_bytes:
                raise PayloadTooLarge(f"The clip is larger than {self.settings.c2_max_clip_bytes // 1024} KB")
            info = wav_info(data)
            if info["channels"] != 1 or info["bits"] != 16 or info["rate"] != 16000:
                raise UnsupportedMedia("The clip must be PCM 16-bit, mono, 16 kHz", {"found": info})  # U5
            if not 4500 <= info["durationMs"] <= 5500:
                raise Validation("The clip must last 4.5 to 5.5 s", {"field": "file", "durationMs": info["durationMs"]})
            captured = parse_iso(ts, "ts") if ts else now
            if not now - timedelta(hours=24) <= captured <= now + timedelta(minutes=5):
                raise Validation("ts must be within the last 24 h", {"field": "ts"})
            tenant_code = str(claims["tenantId"])
            for _ in range(5):
                clip_id = secrets.token_hex(4)
                if await session.get(Clip, clip_id) is None:
                    break
            key = clip_key(tenant_code, vehicle.vehicle_code, captured, clip_id)
            try:
                await self.store.put(key, data)                                   # U7 on failure
            except Exception as exc:  # noqa: BLE001
                log.warning("clip store failed for %s: %s", key, exc)
                raise BadGateway("The clip could not be stored; nothing was kept")
            clip = Clip(clip_id=clip_id, tenant_id=vehicle.tenant_id, vehicle_id=vehicle.vehicle_id,
                        device_id=device.device_id, event_id=event_id, s3_key=key, size_bytes=len(data),
                        duration_ms=info["durationMs"], sample_rate=info["rate"], captured_at=captured,
                        uploaded_at=now, expire_at=captured + timedelta(days=vehicle.retention_days), state="STORED")
            session.add(clip)
            try:
                await session.commit()
            except Exception:
                await self.store.delete(key)
                raise
        log.info("clip %s stored for %s/%s (%s, %d bytes)", clip_id, tenant_code, vehicle.vehicle_code, device_code, len(data))
        return 201, self._upload_body(clip)                                       # U6

    @staticmethod
    def _upload_body(clip: Clip) -> Dict[str, Any]:
        return {"clipId": clip.clip_id, "s3Key": clip.s3_key, "durationMs": clip.duration_ms,
                "sampleRate": clip.sample_rate, "expireAt": iso(clip.expire_at)}

    # -- the worker's fetch -----------------------------------------------------------------------------------
    async def fetch(self, clip_id: str) -> tuple:
        async with self.session_factory() as session:
            clip = await session.get(Clip, clip_id)
            if clip is None:
                raise NotFound("Unknown clip")
            if clip.state != "STORED":
                raise Gone(f"The clip was {clip.state.lower()}")
            vehicle = await session.get(Vehicle, clip.vehicle_id)
        data = await self.store.get(clip.s3_key)
        if data is None:
            raise Gone("The clip object is missing")
        headers = {"X-Tenant-Id": await self.tenants.code_of(clip.tenant_id) or "", "X-Vehicle-Id": vehicle.vehicle_code,
                   "X-Captured-At": iso(clip.captured_at) or "", "X-Clip-Id": clip.clip_id}
        return data, headers

    # -- per-car data ------------------------------------------------------------------------------------------
    async def list_for_vehicle(self, session, vehicle: Vehicle, tenant_code: str, start: datetime, end: datetime,
                               device_code: Optional[str], cls: Optional[str], alert_only: bool,
                               page: int, size: int) -> Dict[str, Any]:
        devices = {d.device_id: d for d in (await session.execute(
            select(Device).where(Device.vehicle_id == vehicle.vehicle_id))).scalars()}
        stmt = select(Clip).where(Clip.tenant_id == vehicle.tenant_id, Clip.vehicle_id == vehicle.vehicle_id,
                                  Clip.captured_at >= start, Clip.captured_at < end).order_by(Clip.captured_at.desc())
        if device_code:
            ids = [i for i, d in devices.items() if d.device_code == device_code]
            stmt = stmt.where(Clip.device_id.in_(ids or [-1]))
        clips = list((await session.execute(stmt)).scalars())
        events = {}
        if clips:
            cursor = self.mongo["audio_events"].find(
                {"tenantId": tenant_code, "vehicleId": vehicle.vehicle_code,
                 "ts": {"$gte": start - timedelta(minutes=5), "$lt": end + timedelta(minutes=5)},
                 "clip.clipId": {"$in": [c.clip_id for c in clips]}})
            for doc in await cursor.to_list(None):
                events[(doc.get("clip") or {}).get("clipId")] = doc
        items = []
        for c in clips:
            doc = events.get(c.clip_id)
            top = ((doc or {}).get("inference") or {}).get("topK") or []
            decision = (doc or {}).get("decision") or {}
            item = {"clipId": c.clip_id, "eventRef": str(doc["_id"]) if doc else None, "ts": iso(c.captured_at),
                    "deviceId": devices[c.device_id].device_code if c.device_id in devices else None,
                    "channel": "interior" if devices.get(c.device_id) and devices[c.device_id].device_type == "MIC_INTERIOR"
                    else "exterior",
                    "topClass": top[0].get("class") if top else None, "confidence": top[0].get("conf") if top else None,
                    "alert": {"alertId": decision.get("alertId"), "type": decision.get("alertType")}
                    if decision.get("alert") else None,
                    "state": c.state, "expireAt": iso(c.expire_at), "durationMs": c.duration_ms,
                    "deletedBy": c.deleted_by, "deletedAt": iso(c.deleted_at)}
            if cls and item["topClass"] != cls:
                continue
            if alert_only and not item["alert"]:
                continue
            items.append(item)
        total = len(items)
        return {"items": items[(page - 1) * size:(page - 1) * size + size], "page": page, "size": size, "total": total}

    # -- playback ---------------------------------------------------------------------------------------------
    def _sig(self, clip_id: str, exp: int) -> str:
        return hmac.new(self.settings.jwt_secret.encode(), f"clip\n{clip_id}\n{exp}".encode(), hashlib.sha256).hexdigest()

    def signed_url(self, clip: Clip) -> Dict[str, Any]:
        exp = int(time.time()) + self.settings.c2_signed_url_ttl_s
        prefix = self.settings.c2_signed_url_prefix.rstrip("/")
        return {"url": f"{prefix}/clips/{clip.clip_id}/file?exp={exp}&sig={self._sig(clip.clip_id, exp)}",
                "expiresAt": iso(datetime.fromtimestamp(exp, timezone.utc))}

    async def signed_file(self, clip_id: str, exp: int, sig: str) -> bytes:
        if exp < int(time.time()) or not hmac.compare_digest(sig, self._sig(clip_id, exp)):
            raise Unauthorized("The link expired or is invalid")
        async with self.session_factory() as session:
            clip = await session.get(Clip, clip_id)
        if clip is None:
            raise NotFound("Unknown clip")
        if clip.state != "STORED":
            raise Gone(f"The clip was {clip.state.lower()}")
        data = await self.store.get(clip.s3_key)
        if data is None:
            raise Gone("The clip object is missing")
        return data

    async def delete(self, session, caller: Caller, clip: Clip, vehicle: Vehicle) -> None:
        """BR-C2-24: the object goes at once; the row stays as DELETED with who and when."""
        if clip.state != "STORED":
            raise Gone(f"The clip was already {clip.state.lower()}")
        await self.store.delete(clip.s3_key)
        clip.state, clip.deleted_by, clip.deleted_at = "DELETED", caller.user_id, utcnow()
        audit(session, caller, vehicle.tenant_id, "DELETE_CLIP", "clip", clip.clip_id, None,
              {"vehicleId": vehicle.vehicle_code})

    # -- retention --------------------------------------------------------------------------------------------
    async def run_retention(self, now: Optional[datetime] = None, batch: int = 500) -> int:
        """Daily job (03:15 UTC): STORED clips past expire_at are removed and marked EXPIRED."""
        now = now or utcnow()
        removed = 0
        while True:
            async with self.session_factory() as session:
                clips = list((await session.execute(select(Clip).where(
                    Clip.state == "STORED", Clip.expire_at < now).limit(batch))).scalars())
                if not clips:
                    break
                for clip in clips:
                    try:
                        await self.store.delete(clip.s3_key)   # a missing object counts as deleted
                    except Exception as exc:  # noqa: BLE001
                        log.warning("retention: could not delete %s: %s", clip.s3_key, exc)
                        continue
                    clip.state = "EXPIRED"
                    removed += 1
                await session.commit()
                if len(clips) < batch:
                    break
        if removed:
            log.info("retention: %d clips expired", removed)
        return removed


def parse_window(from_: Optional[str], to: Optional[str]) -> tuple:
    end = parse_iso(to, "to") if to else utcnow() + timedelta(minutes=1)
    start = parse_iso(from_, "from") if from_ else end - timedelta(days=7)
    if start >= end:
        raise Validation("from must be before to", {"field": "from"})
    return as_naive_utc(start), as_naive_utc(end)
