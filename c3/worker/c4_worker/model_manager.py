"""Which model the worker serves, kept in step with C1's registry (the protocol of stubs/worker.py).

It syncs on start, on MQTT scicp/v1/platform/model/activated and every poll_s (30 s) as the
fallback (5 s while no model is loaded):

  GET /internal/models/active?name=sound-classifier      (404: nothing ACTIVE yet)
  same version and sha256 as loaded  -> POST /internal/worker/status READY (the heartbeat)
  otherwise                          -> LOADING, download the presigned URL, verify size + SHA-256,
                                        load the artifact, swap it in, READY
  any failure                        -> FAILED with the reason; the previous model keeps serving

The swap takes `serve_lock`, which the pipeline holds while it processes one clip, so a new version
only ever takes effect between two clips. `ready` is set once a model is loaded; until then the
pipeline takes nothing from the queue.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import httpx

from .classifiers import ArtifactError, load_artifact
from .clients import C1Client

log = logging.getLogger("c4.models")


def utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class LoadedModel:
    model_id: Optional[int]
    version: str
    tag: str
    sha256: str
    loaded_at: str
    classifier: Any


class ModelManager:
    def __init__(self, c1: C1Client, encoder: Any, cache_dir: Path, http: httpx.AsyncClient,
                 loader: Callable[[Path, Any], Any] = load_artifact) -> None:
        self.c1, self.encoder, self.cache_dir, self.http, self.loader = c1, encoder, Path(cache_dir), http, loader
        self.current: Optional[LoadedModel] = None
        self.loading = False
        self.serve_lock = asyncio.Lock()
        self.ready = asyncio.Event()
        self._wake = asyncio.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # -- what the pipeline and /healthz read -----------------------------------------------------
    @property
    def model_version(self) -> Optional[str]:
        """inference.modelVersion of every result, e.g. sound-classifier-1.2.0."""
        return self.current.tag if self.current else None

    @property
    def state(self) -> str:
        if self.loading:
            return "LOADING"
        return "READY" if self.current else "NO_MODEL"

    # -- one synchronization -------------------------------------------------------------------------
    async def sync(self, reason: str) -> bool:
        """Returns True when C1 had an ACTIVE version (False on 404)."""
        info = await self.c1.active(self.http)
        if info is None:
            log.info("no ACTIVE %s in C1 yet (%s)%s", self.c1.model_name, reason,
                     "" if self.current else "; events wait in the queue")
            return False
        cur = self.current
        if cur and cur.version == info["version"] and cur.sha256 == (info.get("sha256") or "").lower():
            await self.c1.report(self.http, "READY", info["modelId"], info["version"], cur.sha256, cur.loaded_at,
                                 f"serving {info['tag']}")
            return True
        log.info("loading %s (%s)", info["tag"], reason)
        self.loading = True
        try:
            await self.c1.report(self.http, "LOADING", info["modelId"], info["version"], None, None,
                                 f"downloading {info['tag']} ({reason})")
            try:
                sha, size, path = await self.c1.download(self.http, info, self.cache_dir)
            except (httpx.HTTPError, OSError) as exc:
                await self._failed(info, f"download failed: {exc}")
                return True
            if size != info.get("sizeBytes") or sha != (info.get("sha256") or "").lower():
                path.unlink(missing_ok=True)
                await self._failed(info, f"sha256 mismatch ({size} bytes, {sha[:12]})")
                return True
            try:
                classifier = await asyncio.to_thread(self.loader, path, self.encoder)
            except ArtifactError as exc:
                await self._failed(info, f"load failed: {exc}")
                return True
            new = LoadedModel(info.get("modelId"), info["version"], info["tag"], sha, utc_iso(), classifier)
            async with self.serve_lock:  # between two clips
                previous = self.current.tag if self.current else None
                self.current = new
                self.ready.set()
        finally:
            self.loading = False
        await self.c1.report(self.http, "READY", info["modelId"], info["version"], sha, new.loaded_at,
                             f"serving {info['tag']} ({classifier.kind})" + (f" (was {previous})" if previous else ""))
        log.info("READY on %s (%s), sha256 verified (%d bytes)", info["tag"], classifier.kind, size)
        return True

    async def _failed(self, info: Dict[str, Any], detail: str) -> None:
        if self.current:
            detail += f"; still serving {self.current.tag}"
        log.warning("%s %s", info["tag"], detail)
        await self.c1.report(self.http, "FAILED", info["modelId"], info["version"], None, None, detail)

    # -- notifications and the loop -----------------------------------------------------------------
    def wake(self, reason: str = "model.activated") -> None:
        """Thread-safe: called from the MQTT network thread."""
        if self._loop is not None:
            log.info("%s", reason)
            self._loop.call_soon_threadsafe(self._wake.set)

    async def run(self, poll_s: float = 30.0) -> None:
        self._loop = asyncio.get_running_loop()
        reason = "startup"
        while True:
            delay = poll_s
            self._wake.clear()  # cleared before the sync so an activation during it is not lost
            try:
                found = await self.sync(reason)
                if not found and self.current is None:
                    delay = 5.0
            except httpx.HTTPStatusError as exc:
                log.info("C1 answered %s; retrying", exc.response.status_code)
                delay = 5.0 if self.current is None else poll_s
            except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
                log.info("C1 not reachable yet (%s); retrying", exc)
                delay = 5.0 if self.current is None else poll_s
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
                reason = "model.activated"
                await asyncio.sleep(0.2)  # several notifications of one activation become one sync
            except asyncio.TimeoutError:
                reason = "poll"
