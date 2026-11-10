"""Outgoing HTTP calls: C2 clip fetch, C3 result post, and C1's model registry (shapes from stubs/worker.py).

Each function takes an httpx.AsyncClient so the tests can plug in an httpx.MockTransport, and a
`sleep` coroutine so the retry waits can be skipped in tests.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

import httpx

from .audio import ClipError
from .tokens import ServiceToken

log = logging.getLogger("c4.clients")
Sleep = Callable[[float], Awaitable[None]]


# ------------------------------------------------------------------------------------------ C2
async def fetch_clip(client: httpx.AsyncClient, c2_base_url: str, token: ServiceToken, clip_id: str,
                     timeout_s: float = 5.0, retries: int = 2, sleep: Sleep = asyncio.sleep) -> bytes:
    """GET /internal/clips/{clipId}. Retries timeouts, network errors and 5xx; 404 and other 4xx are final."""
    url = f"{c2_base_url}/internal/clips/{clip_id}"
    last = "no attempt"
    for attempt in range(retries + 1):
        if attempt:
            await sleep(float(attempt))
        try:
            r = await client.get(url, headers=token.headers(), timeout=timeout_s)
        except httpx.HTTPError as exc:
            last = f"{type(exc).__name__}: {exc}"
            log.info("clip %s: attempt %d failed (%s)", clip_id, attempt + 1, last)
            continue
        if r.status_code == 200:
            return r.content
        last = f"HTTP {r.status_code} {r.text[:120]}"
        if r.status_code == 404:
            raise ClipError(f"clip {clip_id} not found (404)")
        if r.status_code < 500 and r.status_code != 429:
            raise ClipError(f"clip {clip_id}: {last}")
        log.info("clip %s: attempt %d failed (%s)", clip_id, attempt + 1, last)
    raise ClipError(f"clip {clip_id} not fetched after {retries + 1} attempts: {last}")


# ------------------------------------------------------------------------------------------ C3
async def post_result(client: httpx.AsyncClient, c3_base_url: str, token: ServiceToken, event_ref: str,
                      body: Dict[str, Any], waits: Sequence[float] = (1.0, 2.0, 4.0),
                      sleep: Sleep = asyncio.sleep) -> Optional[Dict[str, Any]]:
    """POST /internal/events/{eventRef}/result: one call plus one retry per wait (network errors and 5xx).

    Returns C3's decision, or None when C3 never accepted the result."""
    url = f"{c3_base_url}/internal/events/{event_ref}/result"
    for attempt in range(len(waits) + 1):
        if attempt:
            await sleep(waits[attempt - 1])
        try:
            r = await client.post(url, json=body, headers=token.headers())
        except httpx.HTTPError as exc:
            log.warning("result %s: attempt %d failed (%s)", event_ref, attempt + 1, exc)
            continue
        if r.status_code < 300:
            data = r.json()
            return data.get("decision", data) if isinstance(data, dict) else data
        if r.status_code < 500 and r.status_code != 429:
            log.error("result %s refused by C3: %s %s", event_ref, r.status_code, r.text[:300])
            return None
        log.warning("result %s: attempt %d got %s", event_ref, attempt + 1, r.status_code)
    log.error("result %s: C3 unreachable after %d attempts", event_ref, len(waits) + 1)
    return None


# ------------------------------------------------------------------------------------------ C1
class C1Client:
    """GET /internal/models/active, the artifact download, POST /internal/worker/status (as stubs/worker.py)."""

    def __init__(self, c1_base_url: str, token: ServiceToken, worker_id: str, model_name: str) -> None:
        self.base_url = c1_base_url.rstrip("/")
        self.token, self.worker_id, self.model_name = token, worker_id, model_name

    def absolute(self, url: str) -> str:
        """A local-storage URL is relative (/api/v1/storage/...): resolve it against C1's host."""
        if urlparse(url).scheme:
            return url
        parsed = urlparse(self.base_url)
        return urljoin(f"{parsed.scheme}://{parsed.netloc}", url)

    async def active(self, client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
        """{modelId, name, version, tag, sha256, sizeBytes, downloadUrl, expiresInSeconds, activatedAt}; None on 404."""
        r = await client.get(f"{self.base_url}/internal/models/active", params={"name": self.model_name},
                             headers=self.token.headers())
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def report(self, client: httpx.AsyncClient, state: str, model_id: Optional[int], version: Optional[str],
                     sha256: Optional[str] = None, loaded_at: Optional[str] = None,
                     detail: Optional[str] = None) -> None:
        if detail and len(detail) > 500:  # C1's WorkerStatusBody: detail max_length=500
            detail = detail[:497] + "..."
        body = {"workerId": self.worker_id, "modelId": model_id, "modelVersion": version, "sha256": sha256,
                "state": state, "loadedAt": loaded_at, "detail": detail}
        try:
            r = await client.post(f"{self.base_url}/internal/worker/status", json=body, headers=self.token.headers())
            if r.status_code >= 400:
                log.warning("C1 refused the worker status: %s %s", r.status_code, r.text[:200])
        except httpx.HTTPError as exc:
            log.warning("C1 unreachable for the worker status: %s", exc)

    async def download(self, client: httpx.AsyncClient, info: Dict[str, Any], cache_dir: Path) -> Tuple[str, int, Path]:
        """Streams the artifact to cache_dir; returns (sha256 hex, size, path)."""
        cache_dir.mkdir(parents=True, exist_ok=True)
        target = cache_dir / f"{info['name']}-{info['version']}.pt"
        digest, size = hashlib.sha256(), 0
        async with client.stream("GET", self.absolute(info["downloadUrl"])) as r:
            r.raise_for_status()
            with open(target, "wb") as handle:
                async for chunk in r.aiter_bytes():
                    digest.update(chunk)
                    size += len(chunk)
                    handle.write(chunk)
        return digest.hexdigest(), size, target
