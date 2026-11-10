"""Shared fixtures, built like C3's (c3/tests/conftest.py):

1. a fresh SQLite file filled by the real C1 seed (seed/seed.py), so the vehicles, users and
   telemetry are exactly what docker compose has;
2. one in-process MongoDB mock shared by the seed and C2;
3. the C2 app with its lifespan (that creates the C2 tables and the seeded cars' devices), a
   recording publisher, a fake MQTT gateway that records what C2 sends, and no timers;
4. an httpx client calling it in process.
"""
from __future__ import annotations

import io
import math
import struct
import sys
import tempfile
import time
import uuid
import wave
from pathlib import Path
from typing import Optional

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, MockTransport, Response

C2_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = C2_DIR.parent
for p in (str(C2_DIR), str(REPO_ROOT), str(REPO_ROOT / "c1" / "api")):
    if p not in sys.path:
        sys.path.insert(0, p)

from c2_service.config import Settings  # noqa: E402
from c2_service.main import create_app  # noqa: E402
from c2_service.services.publisher import RecordingPublisher  # noqa: E402

TEST_DB = Path(tempfile.gettempdir()) / "scicp_c2_test.db"
DB_URL = f"sqlite+aiosqlite:///{TEST_DB}"
JWT_SECRET = "test-jwt-secret-0123456789abcdef0123456789"
SERVICE_SECRET = "test-service-secret-0123456789abcdef01234"
CLIP_DIR = Path(tempfile.gettempdir()) / "scicp_c2_clips"


class FakeGateway:
    """Records what C2 publishes to the cars; online can be switched off."""

    def __init__(self) -> None:
        self.sent: list = []
        self.online = True

    async def publish(self, topic: str, payload: dict, qos: int = 1) -> bool:
        if not self.online:
            return False
        self.sent.append((topic, payload))
        return True

    def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def last(self, kind: str) -> dict:
        return next(p for t, p in reversed(self.sent) if t.endswith("/" + kind))


C1_CALLS: list = []


def _c1_handler(request) -> Response:
    C1_CALLS.append((request.method, str(request.url), request.content))
    return Response(201, json={"created": 4})


def make_settings(**overrides) -> Settings:
    base = dict(database_url=DB_URL, mongo_url="mock", jwt_secret=JWT_SECRET, service_token_secret=SERVICE_SECRET,
                c1_base_url="http://c1.invalid/api/v1", log_level="WARNING", publisher="noop",
                c2_mqtt_enabled=False, c2_background=False, c2_clip_dir=str(CLIP_DIR))
    base.update(overrides)
    return Settings(_env_file=None, **base)


def user_token(user_id: int, role: str, tenant_id: Optional[int], name: str = "Test User") -> str:
    now = int(time.time())
    return jwt.encode({"sub": str(user_id), "tenantId": tenant_id, "role": role, "name": name,
                       "jti": uuid.uuid4().hex, "iat": now, "exp": now + 900}, JWT_SECRET, algorithm="HS256")


def service_token() -> str:
    now = int(time.time())
    return jwt.encode({"sub": "c4-worker", "role": "SERVICE", "iat": now, "exp": now + 3600, "jti": uuid.uuid4().hex},
                      SERVICE_SECRET, algorithm="HS256")


SEED_USERS = {
    "anh": (1001, "OWNER", 1, "Anh Nguyen"), "maria": (1002, "FLEET_MANAGER", 2, "Maria Lopez"),
    "rider": (1003, "RIDER", 1, "Rider Demo"), "iot": (1004, "IOT_ENGINEER", None, "D. Patel"),
    "staff": (1005, "SERVICE_STAFF", None, "Service Staff"), "admin": (1006, "SYS_ADMIN", None, "Platform Admin"),
}


def auth(who: str) -> dict:
    token = service_token() if who == "service" else user_token(*SEED_USERS[who])
    return {"Authorization": f"Bearer {token}"}


def make_wav(seconds: float = 5.0, rate: int = 16000, channels: int = 1, width: int = 2, freq: float = 880.0) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        n = int(seconds * rate)
        frames = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / rate))) * channels
                          for i in range(n)) if width == 2 else bytes(n * channels * width)
        w.writeframes(frames)
    return buf.getvalue()


@pytest_asyncio.fixture(scope="session")
async def mongo():
    from mongomock_motor import AsyncMongoMockClient
    return AsyncMongoMockClient()["scicp"]


@pytest_asyncio.fixture(scope="session")
async def seeded(mongo):
    from seed.seed import run as seed_run
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(f"{TEST_DB}{suffix}").unlink(missing_ok=True)
    return await seed_run(DB_URL, mongo, storage=None)


@pytest.fixture(scope="session")
def gateway():
    return FakeGateway()


@pytest.fixture(scope="session")
def events():
    return RecordingPublisher()


@pytest_asyncio.fixture(scope="session")
async def app(seeded, mongo, gateway, events):
    application = create_app(make_settings(), mongo=mongo, publisher=events, gateway=gateway,
                             c1_transport=MockTransport(_c1_handler))
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture(scope="session")
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://c2.test") as c:
        yield c


@pytest.fixture
def api():
    return "/api/v1"


@pytest.fixture
def services(app):
    return app.state.services
