"""Settings of the C2 service, read from the environment or a .env file.

The variable names are those of the repository's .env.example (DATABASE_URL, MONGO_URL,
JWT_SECRET, SERVICE_TOKEN_SECRET, C1_BASE_URL, MQTT_URL, S3_CLIPS_BUCKET, ...), so C2
reads the same file as the C1 API, the stubs and C3. The few C2-only knobs start with C2_.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Databases (shared with C1 and C3; C2 writes vehicle and vehicle_rider and creates its own tables)
    database_url: str = "sqlite+aiosqlite:///./dev.db"
    mongo_url: str = "mock"
    mongo_db: str = "scicp"

    # Security: user JWTs from C1 (JWT_SECRET), service tokens (SERVICE_TOKEN_SECRET), device tokens (C2)
    jwt_secret: str = "change-me-jwt-secret"
    service_token_secret: str = "change-me-service-secret"
    device_token_secret: Optional[str] = None      # default: derived from SERVICE_TOKEN_SECRET
    device_token_days: int = 180                    # BR-C2-09
    network_secret: Optional[str] = None            # Fernet key material for Wi-Fi passwords; default derived

    # C1: POST /internal/events (device.status) and POST /internal/subscriptions/defaults
    c1_base_url: str = "http://localhost:8000/api/v1"
    publisher: str = "http"                         # http or noop
    client_timeout_seconds: float = 5.0

    # MQTT (Mosquitto): telemetry and status in, config and command out
    mqtt_url: str = "mqtt://localhost:1883"
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None
    c2_mqtt_enabled: bool = True
    c2_mqtt_client_id: str = "c2-api"
    # What the provisioning result tells the car (host and port as seen from the car)
    c2_public_mqtt_host: str = "localhost"
    c2_public_mqtt_port: int = 1883
    c2_public_api_base: str = "http://localhost:8080/c2/api/v1"
    c2_broker_auth: str = "none"                    # none (the course stack's anonymous broker) or dynsec (design)

    # Clip storage: local directory (the "clips" volume) or Amazon S3
    c2_clip_backend: str = "local"
    c2_clip_dir: str = "./clips"
    s3_clips_bucket: str = "scicp-clips"
    s3_region: str = "us-west-2"
    c2_signed_url_prefix: str = "/c2/api/v1"        # where the browser reaches C2 (nginx /c2/)
    c2_signed_url_ttl_s: int = 900

    # Behavior (Section 4 of the C2 design)
    c2_offline_after_s: int = 30                    # BR-C2-19
    c2_sweep_every_s: float = 10.0
    c2_command_timeout_s: int = 30                  # BR-C2-13
    c2_clips_per_minute: int = 12                   # BR-C2-17
    c2_max_clip_bytes: int = 512 * 1024
    c2_retention_hour_utc: int = 3                  # daily job at 03:15 UTC
    c2_background: bool = True                      # sweep, timeouts and retention timers (off in tests)

    api_prefix: str = "/api/v1"
    log_level: str = "INFO"

    @field_validator("database_url", mode="before")
    @classmethod
    def _async_driver(cls, value: str) -> str:
        """.env.example uses sync URLs (mysql+pymysql://); map them to the async drivers, as C1 and C3 do."""
        if value.startswith("mysql+pymysql://"):
            return "mysql+aiomysql://" + value[len("mysql+pymysql://"):]
        if value.startswith("mysql://"):
            return "mysql+aiomysql://" + value[len("mysql://"):]
        if value.startswith("sqlite:///"):
            return "sqlite+aiosqlite:///" + value[len("sqlite:///"):]
        return value

    @property
    def mongo_database_name(self) -> str:
        if self.mongo_url and self.mongo_url != "mock":
            path = self.mongo_url.split("//", 1)[-1].split("/", 1)
            if len(path) == 2:
                name = path[1].split("?", 1)[0]
                if name:
                    return name
        return self.mongo_db

    @property
    def device_secret(self) -> str:
        return self.device_token_secret or hashlib.sha256(f"{self.service_token_secret}:c2-device".encode()).hexdigest()

    @property
    def mqtt_host_port(self) -> tuple:
        raw = self.mqtt_url.split("//", 1)[-1].rstrip("/")
        host, _, port = raw.partition(":")
        return host or "localhost", int(port or 1883)


@lru_cache
def get_settings() -> Settings:
    return Settings()
