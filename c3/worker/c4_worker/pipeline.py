"""One audio event, start to finish, and the loop that takes them from the queue one at a time.

  clip   GET C2 /internal/clips/{clipId} (5 s timeout, 2 retries) -> WAV 16 kHz -> 48 kHz
  model  CLAP audio embedding -> head or zero-shot -> top 3        (in a thread, torch.no_grad)
  store  audio_events insert (a duplicate eventId reuses the stored _id)
  ack    the MQTT message, now that the event is stored
  result POST C3 /internal/events/{_id}/result (retries after 1, 2, 4 s)

When the clip cannot be fetched or decoded the event is stored with inference.error and C3 gets
{tenantId, vehicleId, error, modelVersion} (it answers ERROR_RECORDED). The whole event runs under
the ModelManager's serve_lock, so the model cannot change in the middle of one.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional

import httpx
from pymongo.errors import ConnectionFailure

from .audio import ClipError, decode_wav, resample_to_48k
from .clients import fetch_clip, post_result
from .config import Settings
from .model_manager import ModelManager
from .prio_queue import PriorityEventQueue, QueuedEvent
from .store import build_doc, inference_block, store_event
from .tokens import ServiceToken

log = logging.getLogger("c4.pipeline")


class BadEvent(Exception):
    """The message cannot become an audio_events document (no eventId)."""


class Pipeline:
    def __init__(self, settings: Settings, http: httpx.AsyncClient, db: Any, manager: ModelManager,
                 token: ServiceToken, encoder: Any, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 ack: Optional[Callable[[Optional[int]], None]] = None) -> None:
        self.s, self.http, self.db, self.manager, self.token, self.encoder = settings, http, db, manager, token, encoder
        self.sleep = sleep
        self.ack = ack or (lambda mid: None)
        self.processed = 0
        self.errors = 0

    def _ack(self, item: QueuedEvent) -> None:
        """Acknowledges the item's MQTT message once (redelivered duplicates are deduplicated by eventId)."""
        if item.mid is not None:
            self.ack(item.mid)
            item.mid = None

    def _classify(self, classifier: Any, wav: bytes):
        samples = resample_to_48k(decode_wav(wav))
        return classifier.classify(self.encoder.embed_audio(samples))

    async def process(self, item: QueuedEvent) -> Dict[str, Any]:
        """Returns {eventRef, inserted, body, decision}. The MQTT ack is sent once the event is stored."""
        ev = item.payload
        if not isinstance(ev.get("eventId"), str) or not ev["eventId"]:
            raise BadEvent("no eventId")
        async with self.manager.serve_lock:
            model = self.manager.current
            if model is None:
                raise RuntimeError("process() called with no model loaded")
            start = time.perf_counter()
            clip_id = (ev.get("clip") or {}).get("clipId") if isinstance(ev.get("clip"), dict) else None
            error: Optional[str] = None
            top_k = []
            try:
                if not clip_id:
                    raise ClipError("event has no clip.clipId")
                wav = await fetch_clip(self.http, self.s.c2_base_url, self.token, clip_id, self.s.clip_timeout_s,
                                       self.s.clip_retries, self.sleep)
                top_k = await asyncio.to_thread(self._classify, model.classifier, wav)
            except ClipError as exc:
                error = str(exc)
            latency_ms = int((time.perf_counter() - start) * 1000)
            doc = build_doc(item.tenant_id, item.vehicle_id, ev, inference_block(top_k, model.tag, latency_ms, error))
            event_ref, inserted = await store_event(self.db, doc)
            self._ack(item)
            if error:
                body = {"tenantId": item.tenant_id, "vehicleId": item.vehicle_id, "error": error,
                        "modelVersion": model.tag}
            else:
                body = {"tenantId": item.tenant_id, "vehicleId": item.vehicle_id,
                        "topK": [{"class": c, "conf": p} for c, p in top_k], "modelVersion": model.tag,
                        "latencyMs": latency_ms}
            decision = await post_result(self.http, self.s.c3_base_url, self.token, event_ref, body,
                                         self.s.result_waits, self.sleep)
        self.processed += 1
        if error or decision is None:
            self.errors += 1
        top = f"{top_k[0][0]} {top_k[0][1]:.2f}" if top_k else f"error: {error}"
        log.info("%s/%s event %s -> %s (%s, %d ms, %s)%s; C3: %s", item.tenant_id, item.vehicle_id, ev["eventId"],
                 event_ref, top, latency_ms, model.tag, "" if inserted else " [already stored]", decision)
        return {"eventRef": event_ref, "inserted": inserted, "body": body, "decision": decision}

    async def run(self, queue: PriorityEventQueue) -> None:
        """One event at a time; nothing is taken from the queue while no model is loaded."""
        while True:
            if not self.manager.ready.is_set():
                log.info("no model loaded: %d event(s) waiting in the queue", len(queue))
                await self.manager.ready.wait()
            item = await queue.get()
            try:
                await self.process(item)
            except BadEvent as exc:
                self.errors += 1
                self._ack(item)
                log.warning("%s/%s: dropped a bad event (%s)", item.tenant_id, item.vehicle_id, exc)
            except ConnectionFailure as exc:
                # MongoDB is down: the event is not stored, so it goes back to the queue (not acked).
                log.warning("MongoDB unavailable (%s); retrying event %s in 5 s", exc, item.payload.get("eventId"))
                dropped = queue.put(item)
                if dropped is not None:
                    self._ack(dropped)
                await self.sleep(5.0)
            except Exception:  # noqa: BLE001  one bad event must not stop the worker
                self.errors += 1
                self._ack(item)
                log.exception("event %s failed", item.payload.get("eventId"))
