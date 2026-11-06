"""Shared fixtures. Once per test session:

1. a fresh SQLite file is filled by the real C1 seed (seed/seed.py), so the C1 and C2
   tables and the alert history are exactly what docker compose has;
2. one in-process MongoDB mock is shared by the seed and C3 (the audio events);
3. the C3 app starts with its lifespan (that creates and seeds the C3 tables), and an
   httpx client calls it in process;
4. right then, before any test writes, the database file is copied to SNAPSHOT_DB: the
   action tests run each scenario on a fresh copy of it (fresh_db_copy).

The seed imports C1's `app` package, which is why the test virtualenv also has
c1/api/requirements.txt installed.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

C3_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = C3_DIR.parent
for p in (str(C3_DIR), str(REPO_ROOT), str(REPO_ROOT / "c1" / "api")):
    if p not in sys.path:
        sys.path.insert(0, p)

from c3_service.config import Settings  # noqa: E402
from c3_service.main import create_app  # noqa: E402

TEST_DB = Path(tempfile.gettempdir()) / "scicp_c3_test.db"
SNAPSHOT_DB = Path(tempfile.gettempdir()) / "scicp_c3_snapshot.db"
DB_URL = f"sqlite+aiosqlite:///{TEST_DB}"
JWT_SECRET = "test-jwt-secret-0123456789abcdef0123456789"
SERVICE_SECRET = "test-service-secret-0123456789abcdef01234"


def make_settings(**overrides) -> Settings:
    base = dict(database_url=DB_URL, mongo_url="mock", jwt_secret=JWT_SECRET, service_token_secret=SERVICE_SECRET,
                c1_base_url="http://c1.invalid/api/v1", log_level="WARNING", publisher="noop")
    base.update(overrides)
    return Settings(_env_file=None, **base)


def user_token(user_id: int, role: str, tenant_id: Optional[int], name: str = "Test User",
               secret: str = JWT_SECRET) -> str:
    """A user access token with the claims C1's TokenService.issue_access puts in it."""
    now = int(time.time())
    return jwt.encode({"sub": str(user_id), "tenantId": tenant_id, "role": role, "name": name,
                       "jti": uuid.uuid4().hex, "iat": now, "exp": now + 900}, secret, algorithm="HS256")


def service_token(subject: str = "c1-api", role: str = "SERVICE", secret: str = SERVICE_SECRET) -> str:
    """A service token as C1's TokenService.issue_service_token makes it."""
    now = int(time.time())
    return jwt.encode({"sub": subject, "role": role, "iat": now, "exp": now + 3600, "jti": uuid.uuid4().hex},
                      secret, algorithm="HS256")


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# The seeded users (seed/seed.py USERS): (user_id, role, tenant_id, name)
SEED_USERS = {
    "anh": (1001, "OWNER", 1, "Anh Nguyen"), "maria": (1002, "FLEET_MANAGER", 2, "Maria Lopez"),
    "rider": (1003, "RIDER", 1, "Rider Demo"), "iot": (1004, "IOT_ENGINEER", None, "D. Patel"),
    "staff": (1005, "SERVICE_STAFF", None, "Service Staff"), "admin": (1006, "SYS_ADMIN", None, "Platform Admin"),
}


def token_of(who: str) -> str:
    return user_token(*SEED_USERS[who])


@pytest_asyncio.fixture(scope="session")
async def mongo():
    from mongomock_motor import AsyncMongoMockClient
    return AsyncMongoMockClient()["scicp"]


@pytest_asyncio.fixture(scope="session")
async def seeded(mongo):
    """Runs the C1 seed once on a fresh database file."""
    from seed.seed import run as seed_run
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(f"{TEST_DB}{suffix}").unlink(missing_ok=True)
    return await seed_run(DB_URL, mongo, storage=None)


@pytest_asyncio.fixture(scope="session")
async def app(seeded, mongo):
    application = create_app(make_settings(), mongo=mongo)
    async with application.router.lifespan_context(application):
        shutil.copyfile(TEST_DB, SNAPSHOT_DB)  # pristine: seeded, C3 tables created, nothing changed yet
        yield application


@pytest.fixture
def fresh_db_copy(app, tmp_path):
    """Returns a function that copies the pristine snapshot to a new file and gives its path."""
    def copy(name: str = "c3.db") -> Path:
        target = tmp_path / name
        shutil.copyfile(SNAPSHOT_DB, target)
        return target
    return copy


def sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path}"


@pytest_asyncio.fixture(scope="session")
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://c3.test") as c:
        yield c


@pytest.fixture
def api():
    return "/api/v1"
