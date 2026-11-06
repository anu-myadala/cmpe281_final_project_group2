"""Settings of the C3 service, read from the environment or a .env file.

The variable names are the ones of the repository's .env.example (DATABASE_URL,
MONGO_URL, JWT_SECRET, SERVICE_TOKEN_SECRET, C1_BASE_URL, ...), so C3 reads the
same file as the C1 API and the stubs.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Databases (shared with C1: C3 reads its tables and writes only its own)
    database_url: str = "sqlite+aiosqlite:///./dev.db"
    mongo_url: str = "mock"
    mongo_db: str = "scicp"

    # Security: user JWTs are signed by C1 with JWT_SECRET, service tokens with SERVICE_TOKEN_SECRET
    jwt_secret: str = "change-me-jwt-secret"
    service_token_secret: str = "change-me-service-secret"

    # C1: POST /internal/events (alert.created / alert.updated) and the feedback labels
    c1_base_url: str = "http://localhost:8000/api/v1"
    client_timeout_seconds: float = 5.0
    # How alert events reach C1: http (POST /internal/events, the default) or noop (dropped, logged)
    publisher: str = "http"
    publish_timeout_seconds: float = 2.0

    api_prefix: str = "/api/v1"
    log_level: str = "INFO"

    @field_validator("database_url", mode="before")
    @classmethod
    def _async_driver(cls, value: str) -> str:
        """.env.example uses sync URLs (mysql+pymysql://); map them to the async drivers, as C1 does."""
        if value.startswith("mysql+pymysql://"):
            return "mysql+aiomysql://" + value[len("mysql+pymysql://"):]
        if value.startswith("mysql://"):
            return "mysql+aiomysql://" + value[len("mysql://"):]
        if value.startswith("sqlite:///"):
            return "sqlite+aiosqlite:///" + value[len("sqlite:///"):]
        return value

    @field_validator("publisher", mode="before")
    @classmethod
    def _publisher(cls, value) -> str:
        value = str(value or "http").strip().lower()
        if value not in ("http", "noop"):
            raise ValueError("PUBLISHER must be http or noop")
        return value

    @property
    def mongo_database_name(self) -> str:
        """The database name in the MongoDB URL path (mongodb://mongo:27017/scicp), else MONGO_DB."""
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
