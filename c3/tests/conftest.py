"""Shared fixtures: a C3 app on a throw-away SQLite file and the MongoDB mock,
with its lifespan running, and an httpx client that calls it in process."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

C3_DIR = Path(__file__).resolve().parents[1]
if str(C3_DIR) not in sys.path:
    sys.path.insert(0, str(C3_DIR))

from c3_service.config import Settings  # noqa: E402
from c3_service.main import create_app  # noqa: E402

TEST_DB = Path(tempfile.gettempdir()) / "scicp_c3_test.db"
JWT_SECRET = "test-jwt-secret-0123456789abcdef0123456789"
SERVICE_SECRET = "test-service-secret-0123456789abcdef01234"


def test_settings(**overrides) -> Settings:
    base = dict(database_url=f"sqlite+aiosqlite:///{TEST_DB}", mongo_url="mock", jwt_secret=JWT_SECRET,
                service_token_secret=SERVICE_SECRET, c1_base_url="http://c1.invalid/api/v1", log_level="WARNING")
    base.update(overrides)
    return Settings(_env_file=None, **base)


test_settings.__test__ = False  # not a test, despite the name


@pytest_asyncio.fixture(scope="session")
async def app():
    application = create_app(test_settings())
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture(scope="session")
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://c3.test") as c:
        yield c


@pytest.fixture
def api():
    return "/api/v1"
