"""Simulated C4 classifier worker: the deployment side of the model registry.

The real worker (C3/C4) runs the CLAP + head classifier on the audio clips. This
stand-in does everything around the model except the inference itself:

  * it learns about a new ACTIVE version in three ways, as in the design:
      - inline mode: the EventBus topic model.activated,
      - compose: the MQTT topic scicp/v1/platform/model/activated on Mosquitto
        (QoS 1, retained, so a worker that starts late still sees the last activation),
      - and in any case a poll of C1 every WORKER_POLL_S (30 s) as the fallback;
  * it asks C1 which version is ACTIVE (GET /internal/models/active, service token),
    downloads the artifact through the presigned URL (a relative URL is resolved
    against C1_BASE_URL's host), verifies size and SHA-256, and "loads" it;
  * it reports every step to C1 (POST /internal/worker/status: LOADING, then READY
    or FAILED), and repeats READY as a heartbeat on every poll.

When a download fails the verification the worker keeps serving the version it had,
which is what the generator then stamps on new audio events.

Environment: C1_BASE_URL (default http://127.0.0.1:8000/api/v1), SERVICE_TOKEN_SECRET,
MQTT_URL / MQTT_USERNAME / MQTT_PASSWORD (optional), WORKER_ENABLED=1, WORKER_ID=worker-1,
WORKER_POLL_S=30, WORKER_LOAD_DELAY_S=2 (simulated model load time), WORKER_MODEL=sound-classifier.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlparse

import httpx
import jwt

log = logging.getLogger("scicp.worker")

MODEL_ACTIVATED_TOPIC = "scicp/v1/platform/model/activated"
DEFAULT_C1_BASE_URL = "http://127.0.0.1:8000/api/v1"


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


class ClassifierWorker:
    def __init__(self, c1_base_url: str, service_token_secret: str, worker_id: str = "worker-1",
                 model_name: str = "sound-classifier", bus: Any = None, mqtt_url: Optional[str] = None,
                 mqtt_username: Optional[str] = None, mqtt_password: Optional[str] = None,
                 poll_s: float = 30.0, load_delay_s: float = 2.0, timeout: float = 15.0,
                 cache_dir: Optional[str] = None) -> None:
        self.base_url = c1_base_url.rstrip("/")
        self.secret = service_token_secret
        self.worker_id = worker_id
        self.model_name = model_name
        self.bus = bus
        self.mqtt_url = mqtt_url or None
        self.mqtt_username = mqtt_username or None
        self.mqtt_password = mqtt_password or None
        self.poll_s = poll_s
        self.load_delay_s = load_delay_s
        self.timeout = timeout
        self.cache_dir = Path(cache_dir or Path(tempfile.gettempdir()) / "scicp-worker")
        # What the worker serves: {modelId, version, tag, sha256, loadedAt}
        self.loaded: Optional[Dict[str, Any]] = None
        self._wake: Optional[asyncio.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._mqtt = None
        self._token: Optional[str] = None
        self._token_exp = 0

    @classmethod
    def from_env(cls, bus: Any = None, service_token_secret: Optional[str] = None) -> "ClassifierWorker":
        secret = service_token_secret or os.environ.get("SERVICE_TOKEN_SECRET", "change-me-service-secret")
        return cls(c1_base_url=os.environ.get("C1_BASE_URL", DEFAULT_C1_BASE_URL), service_token_secret=secret,
                   worker_id=os.environ.get("WORKER_ID", "worker-1"),
                   model_name=os.environ.get("WORKER_MODEL", "sound-classifier"), bus=bus,
                   mqtt_url=os.environ.get("MQTT_URL"), mqtt_username=os.environ.get("MQTT_USERNAME"),
                   mqtt_password=os.environ.get("MQTT_PASSWORD"), poll_s=max(5.0, _env_float("WORKER_POLL_S", 30.0)),
                   load_delay_s=max(0.0, _env_float("WORKER_LOAD_DELAY_S", 2.0)))

    # -- what the generator needs ------------------------------------------------------------
    @property
    def model_tag(self) -> Optional[str]:
        """inference.modelVersion of new audio events, e.g. sound-classifier-1.2.0."""
        return self.loaded["tag"] if self.loaded else None

    # -- C1 calls --------------------------------------------------------------------------------
    def _service_token(self) -> str:
        now = int(time.time())
        if self._token is None or now > self._token_exp - 300:
            self._token_exp = now + 24 * 3600
            self._token = jwt.encode({"sub": f"c4-{self.worker_id}", "role": "SERVICE", "iat": now,
                                      "exp": self._token_exp, "jti": uuid.uuid4().hex}, self.secret, algorithm="HS256")
        return self._token

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._service_token()}"}

    def _absolute(self, url: str) -> str:
        """A local-storage URL is relative (/api/v1/storage/...): resolve it against C1's host."""
        if urlparse(url).scheme:
            return url
        parsed = urlparse(self.base_url)
        return urljoin(f"{parsed.scheme}://{parsed.netloc}", url)

    async def _active(self, client: httpx.AsyncClient) -> Dict[str, Any]:
        response = await client.get(f"{self.base_url}/internal/models/active", params={"name": self.model_name},
                                    headers=self._headers())
        response.raise_for_status()
        return response.json()

    async def report(self, client: httpx.AsyncClient, state: str, model_id: Optional[int], version: Optional[str],
                     sha256: Optional[str] = None, loaded_at: Optional[str] = None,
                     detail: Optional[str] = None) -> None:
        body = {"workerId": self.worker_id, "modelId": model_id, "modelVersion": version, "sha256": sha256,
                "state": state, "loadedAt": loaded_at, "detail": detail}
        try:
            response = await client.post(f"{self.base_url}/internal/worker/status", json=body, headers=self._headers())
            if response.status_code >= 400:
                log.warning("C1 refused the worker status: %s %s", response.status_code, response.text[:200])
        except httpx.HTTPError as exc:
            log.warning("C1 unreachable for the worker status: %s", exc)

    async def _download(self, client: httpx.AsyncClient, info: Dict[str, Any]) -> tuple:
        """Streams the artifact to the cache directory; returns (sha256 hex, size, path)."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / f"{info['name']}-{info['version']}.pt"
        digest = hashlib.sha256()
        size = 0
        async with client.stream("GET", self._absolute(info["downloadUrl"])) as response:
            response.raise_for_status()
            with open(target, "wb") as handle:
                async for chunk in response.aiter_bytes():
                    digest.update(chunk)
                    size += len(chunk)
                    handle.write(chunk)
        return digest.hexdigest(), size, target

    # -- one synchronization ---------------------------------------------------------------------------
    async def sync(self, reason: str) -> None:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            info = await self._active(client)
            if self.loaded and self.loaded["version"] == info["version"] and self.loaded["sha256"] == info["sha256"]:
                # Nothing new: the READY report doubles as the heartbeat.
                await self.report(client, "READY", info["modelId"], info["version"], self.loaded["sha256"],
                                  self.loaded["loadedAt"], f"serving {info['tag']}")
                return
            log.info("worker %s: loading %s (%s)", self.worker_id, info["tag"], reason)
            await self.report(client, "LOADING", info["modelId"], info["version"], None, None,
                              f"downloading {info['tag']} ({reason})")
            try:
                sha, size, path = await self._download(client, info)
            except httpx.HTTPError as exc:
                await self._failed(client, info, f"download failed: {exc}")
                return
            if size != info.get("sizeBytes") or sha != (info.get("sha256") or "").lower():
                path.unlink(missing_ok=True)
                await self._failed(client, info, f"sha256 mismatch ({size} bytes, {sha[:12]})")
                return
            # A real worker would deserialize the weights here; the pause makes LOADING visible.
            await asyncio.sleep(self.load_delay_s)
            previous = self.loaded["tag"] if self.loaded else None
            self.loaded = {"modelId": info["modelId"], "version": info["version"], "tag": info["tag"], "sha256": sha,
                           "loadedAt": _utc_iso()}
            await self.report(client, "READY", info["modelId"], info["version"], sha, self.loaded["loadedAt"],
                              f"serving {info['tag']}" + (f" (was {previous})" if previous else ""))
            log.info("worker %s: READY on %s, sha256 verified (%d bytes)", self.worker_id, info["tag"], size)

    async def _failed(self, client: httpx.AsyncClient, info: Dict[str, Any], detail: str) -> None:
        if self.loaded:
            detail += f"; still serving {self.loaded['tag']}"
        log.warning("worker %s: %s %s", self.worker_id, info["tag"], detail)
        await self.report(client, "FAILED", info["modelId"], info["version"], None, None, detail)

    # -- notifications -------------------------------------------------------------------------------
    def wake(self, reason: str = "model.activated") -> None:
        """Thread-safe: called from the EventBus handler or from the MQTT network thread."""
        if self._loop is None or self._wake is None:
            return
        log.info("worker %s: %s", self.worker_id, reason)
        self._loop.call_soon_threadsafe(self._wake.set)

    async def _on_bus_event(self, topic: str, payload: Dict[str, Any]) -> None:
        self.wake(f"{topic} {payload.get('version')} on the EventBus")

    def _start_mqtt(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            log.info("paho-mqtt missing: the worker relies on polling")
            return
        parsed = urlparse(self.mqtt_url or "")
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"scicp-c4-{self.worker_id}")
        if self.mqtt_username:
            client.username_pw_set(self.mqtt_username, self.mqtt_password)
        if parsed.scheme in ("mqtts", "ssl"):
            client.tls_set()

        def on_connect(cli, userdata, flags, reason_code, properties=None):
            if reason_code == 0:
                cli.subscribe(MODEL_ACTIVATED_TOPIC, qos=1)
                log.info("worker %s: subscribed to %s", self.worker_id, MODEL_ACTIVATED_TOPIC)
            else:
                log.warning("worker %s: MQTT connect refused (%s)", self.worker_id, reason_code)

        def on_message(cli, userdata, message):
            try:
                payload = json.loads(message.payload.decode() or "{}")
            except ValueError:
                payload = {}
            retained = " (retained)" if message.retain else ""
            self.wake(f"MQTT {message.topic} {payload.get('version')}{retained}")

        client.on_connect = on_connect
        client.on_message = on_message
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        port = parsed.port or (8883 if parsed.scheme in ("mqtts", "ssl") else 1883)
        # connect_async + loop_start: the network thread connects and reconnects on its own.
        client.connect_async(parsed.hostname or "localhost", port, keepalive=30)
        client.loop_start()
        self._mqtt = client

    # -- main loop ----------------------------------------------------------------------------------
    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        if self.bus is not None and hasattr(self.bus, "subscribe"):
            self.bus.subscribe("model.activated", self._on_bus_event)
            log.info("worker %s: listening to model.activated on the EventBus", self.worker_id)
        if self.mqtt_url:
            self._start_mqtt()
        log.info("worker %s: C1 at %s, polling every %.0f s", self.worker_id, self.base_url, self.poll_s)
        reason = "startup"
        try:
            while True:
                delay = self.poll_s
                # Cleared before the sync so that an activation arriving during it is not lost.
                self._wake.clear()
                try:
                    await self.sync(reason)
                except httpx.HTTPStatusError as exc:
                    log.info("worker %s: C1 answered %s; retrying", self.worker_id, exc.response.status_code)
                    delay = 5.0 if self.loaded is None else self.poll_s
                except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
                    log.info("worker %s: C1 not reachable yet (%s); retrying", self.worker_id, exc)
                    delay = 5.0 if self.loaded is None else self.poll_s
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=delay)
                    reason = "model.activated"
                    await asyncio.sleep(0.2)  # several notifications of one activation become one sync
                except asyncio.TimeoutError:
                    reason = "poll"
        finally:
            if self._mqtt is not None:
                self._mqtt.loop_stop()
                try:
                    self._mqtt.disconnect()
                except Exception:  # noqa: BLE001
                    pass


__all__ = ["ClassifierWorker", "MODEL_ACTIVATED_TOPIC"]
