"""Application settings, read from the environment (or a .env file).

The variable names are the ones listed in docs/BRIEF.md so that the same
.env file works for docker compose, for the EC2 deployment and for the
single-process developer mode.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, List, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Databases
    database_url: str = "sqlite+aiosqlite:///./dev.db"
    mongo_url: str = "mock"
    mongo_db: str = "scicp"

    # Security
    jwt_secret: str = "change-me-jwt-secret"
    service_token_secret: str = "change-me-service-secret"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    service_token_hours: int = 24
    cookie_secure: bool = False
    login_rate_limit_per_minute: int = 10

    # Object storage for the model artifacts. "local" keeps them on a volume of the API
    # container (docker compose on a laptop); "s3" uses Amazon S3 (EC2 with the instance role).
    storage_backend: str = "local"  # local | s3
    storage_dir: str = "/data/objects"
    s3_endpoint_url: Optional[str] = None
    s3_public_endpoint_url: Optional[str] = None
    s3_access_key: Optional[str] = None
    s3_secret_key: Optional[str] = None
    s3_region: str = "us-west-2"
    s3_models_bucket: str = "scicp-models"
    presign_seconds: int = 900

    # Mail
    mail_mode: str = "log"  # smtp | ses | log
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    mail_from: str = "no-reply@scicp.example"

    # MQTT (optional)
    mqtt_url: Optional[str] = None
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None

    # Neighboring components
    c2_base_url: str = "http://localhost:8002/api/v1"
    c3_base_url: str = "http://localhost:8003/api/v1"
    client_timeout_seconds: float = 5.0
    overview_cache_seconds: int = 30
    map_cache_seconds: int = 10
    # When C2/C3 are down and no cache exists the overview answers 502 (design).
    # Set to true to build it from the local vehicle/alert tables instead (degraded: true).
    overview_local_fallback: bool = False

    # Web
    # Comma-separated in the environment (NoDecode: not parsed as JSON before _split_origins).
    cors_origins: Annotated[List[str], NoDecode] = ["http://localhost:5173", "http://localhost:8080"]
    api_prefix: str = "/api/v1"

    # Developer / CI mode
    stubs_inline: bool = False
    seed_on_start: bool = False
    log_level: str = "INFO"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value):
        if isinstance(value, str):
            return [o.strip() for o in value.split(",") if o.strip()]
        return value

    @field_validator("storage_backend", mode="before")
    @classmethod
    def _backend(cls, value):
        value = str(value or "local").strip().lower()
        if value not in ("local", "s3"):
            raise ValueError("STORAGE_BACKEND must be local or s3")
        return value

    @field_validator("s3_endpoint_url", "s3_public_endpoint_url", "s3_access_key", "s3_secret_key", mode="before")
    @classmethod
    def _empty_is_none(cls, value):
        """docker-compose.aws.yml blanks these out; an empty string means "not set"."""
        return value or None

    @field_validator("database_url", mode="before")
    @classmethod
    def _async_driver(cls, value: str) -> str:
        """Accept the sync URLs of the brief and map them to the async drivers."""
        if value.startswith("mysql+pymysql://"):
            return "mysql+aiomysql://" + value[len("mysql+pymysql://"):]
        if value.startswith("mysql://"):
            return "mysql+aiomysql://" + value[len("mysql://"):]
        if value.startswith("sqlite:///"):
            return "sqlite+aiosqlite:///" + value[len("sqlite:///"):]
        return value

    @property
    def mongo_database_name(self) -> str:
        """Database name from the MongoDB URL path, falling back to MONGO_DB."""
        if self.mongo_url and self.mongo_url != "mock":
            path = self.mongo_url.split("//", 1)[-1].split("/", 1)
            if len(path) == 2:
                name = path[1].split("?", 1)[0]
                if name:
                    return name
        return self.mongo_db


@lru_cache
def get_settings() -> Settings:
    return Settings()
