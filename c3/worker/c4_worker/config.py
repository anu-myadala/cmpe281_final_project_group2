"""Every setting of the worker, read once from the environment (docker-compose.c4.yml + .env)."""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

CLASSES = ("siren", "crash", "glass_break", "scream", "honking", "traffic", "environmental")
EVENT_TOPIC = "scicp/v1/+/+/audio/event"
MODEL_ACTIVATED_TOPIC = "scicp/v1/platform/model/activated"


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    service_token_secret: str
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    c1_base_url: str = "http://127.0.0.1:8000/api/v1"
    c2_base_url: str = "http://127.0.0.1:8012/api/v1"
    c3_base_url: str = "http://127.0.0.1:8003/api/v1"
    mongo_url: str = "mongodb://localhost:27017/scicp"
    worker_id: str = "c4-worker-1"
    token_sub: str = "c4-worker"
    model_name: str = "sound-classifier"
    poll_s: float = 30.0
    queue_size: int = 200
    health_port: int = 8010
    clip_timeout_s: float = 5.0
    clip_retries: int = 2
    result_waits: tuple = (1.0, 2.0, 4.0)
    cache_dir: str = "/tmp/c4-models"
    clap_model: str = "laion/clap-htsat-unfused"
    # Several workers: with a group name the audio events are taken with an MQTT shared subscription
    # ($share/<group>/...), so each clip is classified by ONE worker of the pool. Model activations
    # stay a plain subscription: every worker must load the new version.
    share_group: str = ""

    @property
    def event_subscription(self) -> str:
        return f"$share/{self.share_group}/{EVENT_TOPIC}" if self.share_group else EVENT_TOPIC

    @property
    def mongo_db_name(self) -> str:
        """Same rule as c3/tools/send_result.py database_name()."""
        path = self.mongo_url.split("//", 1)[-1].split("/", 1)
        return (path[1].split("?", 1)[0] if len(path) == 2 else "") or "scicp"

    @classmethod
    def from_env(cls) -> "Settings":
        secret = os.environ.get("SERVICE_TOKEN_SECRET")
        if not secret:
            raise SystemExit("SERVICE_TOKEN_SECRET is not set")
        host: Optional[str] = os.environ.get("MQTT_HOST")
        port = os.environ.get("MQTT_PORT")
        if not host and os.environ.get("MQTT_URL"):  # .env carries MQTT_URL=mqtt://host:port
            parsed = urlparse(os.environ["MQTT_URL"])
            host, port = parsed.hostname, port or (str(parsed.port) if parsed.port else None)
        return cls(
            service_token_secret=secret,
            mqtt_host=host or "localhost",
            mqtt_port=int(port or 1883),
            c1_base_url=os.environ.get("C1_BASE_URL", cls.c1_base_url).rstrip("/"),
            c2_base_url=os.environ.get("C2_BASE_URL", cls.c2_base_url).rstrip("/"),
            c3_base_url=os.environ.get("C3_BASE_URL", cls.c3_base_url).rstrip("/"),
            mongo_url=os.environ.get("MONGO_URL", cls.mongo_url),
            # WORKER_ID=auto names the worker after its container, for a pool of replicas.
            worker_id=(f"c4-{socket.gethostname()}" if os.environ.get("WORKER_ID") == "auto"
                       else os.environ.get("WORKER_ID", cls.worker_id)),
            share_group=os.environ.get("C4_SHARE_GROUP", ""),
            model_name=os.environ.get("WORKER_MODEL", cls.model_name),
            poll_s=max(5.0, _float("WORKER_POLL_S", 30.0)),
            health_port=int(os.environ.get("HEALTH_PORT", cls.health_port)),
            cache_dir=os.environ.get("MODEL_CACHE_DIR", cls.cache_dir),
            clap_model=os.environ.get("CLAP_MODEL", cls.clap_model),
        )
