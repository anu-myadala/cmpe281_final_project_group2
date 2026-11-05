"""Shared fixtures. Once per test session:

1. a fresh SQLite file is filled by the real C1 seed (seed/seed.py), so the C1 and C2
   tables and the alert history are exactly what docker compose has;
2. one in-process MongoDB mock is shared by the seed and C3 (the audio events);
3. the C3 app starts with its lifespan (that creates and seeds the C3 tables), and an
   httpx client calls it in process.

The seed imports C1's `app` package, which is why the test virtualenv also has
c1/api/requirements.txt installed.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

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
DB_URL = f"sqlite+aiosqlite:///{TEST_DB}"
JWT_SECRET = "test-jwt-secret-0123456789abcdef0123456789"
SERVICE_SECRET = "test-service-secret-0123456789abcdef01234"


def make_settings(**overrides) -> Settings:
    base = dict(database_url=DB_URL, mongo_url="mock", jwt_secret=JWT_SECRET, service_token_secret=SERVICE_SECRET,
                c1_base_url="http://c1.invalid/api/v1", log_level="WARNING")
    base.update(overrides)
    return Settings(_env_file=None, **base)


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
        yield application


@pytest_asyncio.fixture(scope="session")
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://c3.test") as c:
        yield c


@pytest.fixture
def api():
    return "/api/v1"
