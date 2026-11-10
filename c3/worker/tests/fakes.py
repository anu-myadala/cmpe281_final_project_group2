"""Fakes for the unit tests: an encoder without CLAP, a head that picks a known class, WAV bytes,
artifact files, and in-memory C1 / C2 / C3 behind one httpx.MockTransport."""
from __future__ import annotations

import hashlib
import io
import json
import math
import struct
import wave
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import httpx
import numpy as np
import torch

from c4_worker.config import CLASSES, Settings
from c4_worker.classifiers import build_head

SECRET = "test-service-secret"
C1 = "http://api:8000/api/v1"
C2 = "http://c2:8002/api/v1"
C3 = "http://c3:8003/api/v1"


def settings(**kw) -> Settings:
    base = dict(service_token_secret=SECRET, c1_base_url=C1, c2_base_url=C2, c3_base_url=C3,
                mongo_url="mongodb://mongo:27017/scicp")
    base.update(kw)
    return Settings(**base)


def basis(cls: str, dim: int = 512) -> torch.Tensor:
    v = torch.zeros(dim)
    v[CLASSES.index(cls)] = 1.0
    return v


class FakeEncoder:
    """embed_audio returns the basis vector of `audio_class`; a text maps to the class named inside it."""
    dim = 512

    def __init__(self, audio_class: str = "crash") -> None:
        self.audio_class = audio_class
        self.audio_calls: List[int] = []   # number of samples of each clip (48 kHz)
        self.text_calls: List[List[str]] = []

    def embed_audio(self, samples_48k: np.ndarray) -> torch.Tensor:
        self.audio_calls.append(len(samples_48k))
        return basis(self.audio_class) * 3.0  # not normalized, like CLAP

    def embed_text(self, texts: Sequence[str]) -> torch.Tensor:
        self.text_calls.append(list(texts))
        rows = []
        for t in texts:
            cls = next(c for c in CLASSES if c in t)
            rows.append(basis(cls) + 0.01 * torch.ones(self.dim))
        return torch.stack(rows)


def head_state_dict(scale: float = 10.0) -> Dict[str, torch.Tensor]:
    """A head where hidden unit i copies input i and logit i reads hidden unit i: basis(c) -> class c."""
    net = build_head()
    sd = {k: torch.zeros_like(v) for k, v in net.state_dict().items()}
    for i in range(len(CLASSES)):
        sd["0.weight"][i, i] = 1.0
        sd["3.weight"][i, i] = scale
    return sd


def write_head(path: Path, **overrides) -> Path:
    data = {"kind": "head", "state_dict": head_state_dict(), "classes": list(CLASSES), "embedding_dim": 512}
    data.update(overrides)
    torch.save(data, path)
    return path


def write_zero_shot(path: Path, **overrides) -> Path:
    data = {"kind": "zero_shot", "classes": list(CLASSES),
            "prompts": {c: [f"a {c} sound", f"the noise of {c}"] for c in CLASSES}, "temperature": 0.05}
    data.update(overrides)
    torch.save(data, path)
    return path


def wav_bytes(seconds: float = 5.0, rate: int = 16000, channels: int = 1, width: int = 2, hz: float = 440.0) -> bytes:
    n = int(seconds * rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        if width == 2:
            frames = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * hz * i / rate))) * channels
                              for i in range(n))
        else:
            frames = bytes([128]) * n * channels
        w.writeframes(frames)
    return buf.getvalue()


def event(event_id: str = "0b8e7a52-5b8e-4a55-9a3c-1f6f8d2c1a01", clip_id: str = "clip-0001",
          priority: str = "HIGH", tenant: str = "tA", vehicle: str = "AV-01", **extra) -> Dict[str, Any]:
    """Shaped like C2's edge agent message (c2/edge/agent.py)."""
    ev = {"eventId": event_id, "tenantId": tenant, "vehicleId": vehicle, "deviceId": "dev-0112",
          "channel": "exterior", "ts": "2026-11-09T18:30:05Z",
          "clip": {"clipId": clip_id, "s3Key": f"tenants/{tenant}/vehicles/{vehicle}/2026/11/09/{clip_id}.wav",
                   "durationMs": 5000, "sampleRate": 16000},
          "edge": {"class": "crash", "score": 0.71, "priority": priority},
          "location": {"type": "Point", "coordinates": [-121.8811, 37.3352]},
          "context": {"speedKph": 48.0, "driveMode": "AUTONOMOUS", "ignition": True}}
    ev.update(extra)
    return ev


class FakeServices:
    """C1 + C2 + C3 in memory. Records every request; behavior is tweaked per test through attributes."""

    def __init__(self) -> None:
        self.requests: List[httpx.Request] = []
        self.status_reports: List[Dict[str, Any]] = []
        self.results: List[Dict[str, Any]] = []        # {"eventRef", "body"}
        self.clips: Dict[str, bytes] = {}
        self.clip_failures: Dict[str, List[Any]] = {}  # clipId -> statuses / exceptions to return first
        self.result_failures: List[Any] = []
        self.active: Optional[Dict[str, Any]] = None   # C1 /internal/models/active (None -> 404)
        self.artifacts: Dict[str, bytes] = {}          # download path -> bytes
        self.on_clip: Optional[Callable[[str], Any]] = None
        self.log: List[str] = []                       # ordered trace: clip, result, status

    def set_active(self, artifact: bytes, version: str = "1.3.0", model_id: int = 1, sha256: Optional[str] = None,
                   size: Optional[int] = None, url: Optional[str] = None) -> Dict[str, Any]:
        path = url or f"/api/v1/storage/objects/models/sound-classifier/{version}/model.pt?op=get&exp=1&sig=x"
        self.artifacts[path.split("?")[0]] = artifact
        self.active = {"modelId": model_id, "name": "sound-classifier", "version": version,
                       "tag": f"sound-classifier-{version}",
                       "sha256": sha256 if sha256 is not None else hashlib.sha256(artifact).hexdigest(),
                       "sizeBytes": size if size is not None else len(artifact), "downloadUrl": path,
                       "expiresInSeconds": 900, "activatedAt": "2026-11-09T18:00:00Z"}
        return self.active

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        path = request.url.path
        if url.startswith(C2 + "/internal/clips/"):
            clip_id = path.rsplit("/", 1)[-1]
            self.log.append(f"clip {clip_id}")
            if self.on_clip:
                await self.on_clip(clip_id)
            queued = self.clip_failures.get(clip_id)
            if queued:
                f = queued.pop(0)
                if isinstance(f, Exception):
                    raise f
                return httpx.Response(f, json={"error": {"code": "X"}})
            if clip_id in self.clips:
                return httpx.Response(200, content=self.clips[clip_id], headers={"content-type": "audio/wav"})
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "clip not found"}})
        if url.startswith(C3 + "/internal/events/") and path.endswith("/result"):
            body = json.loads(request.content)
            ref = path.split("/")[-2]
            self.log.append(f"result {ref}")
            if self.result_failures:
                f = self.result_failures.pop(0)
                if isinstance(f, Exception):
                    raise f
                return httpx.Response(f, json={"error": {"code": "X"}})
            self.results.append({"eventRef": ref, "body": body})
            if body.get("error"):
                decision = {"alert": False, "reason": "ERROR_RECORDED"}
            else:
                decision = {"alert": True, "alertId": 30001, "alertType": "ACCIDENT", "severity": "CRITICAL",
                            "reason": "ALERT_CREATED"}
            return httpx.Response(200, json={"decision": decision})
        if url.startswith(C1 + "/internal/models/active"):
            if self.active is None:
                return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "no ACTIVE version"}})
            return httpx.Response(200, json=self.active)
        if url.startswith(C1 + "/internal/worker/status"):
            body = json.loads(request.content)
            if body.get("detail") and len(body["detail"]) > 500:   # as C1's WorkerStatusBody
                return httpx.Response(400, json={"error": {"code": "VALIDATION", "message": "detail > 500"}})
            self.status_reports.append(body)
            self.log.append(f"status {body['state']}")
            return httpx.Response(200, json={**body, "sha256Verified": body["state"] == "READY"})
        if path in self.artifacts:
            return httpx.Response(200, content=self.artifacts[path])
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def states(self) -> List[str]:
        return [r["state"] for r in self.status_reports]

