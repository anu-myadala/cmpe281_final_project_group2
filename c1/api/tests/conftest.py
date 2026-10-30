"""Shared fixtures: one seeded app (SQLite + mock MongoDB) for the whole
session, an httpx client, fake C2/C3 clients and an in-memory StorageClient."""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

API_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = API_DIR.parents[1]
for p in (str(API_DIR), str(REPO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.clients import ClientError, HeadInfo  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

TEST_DB = Path(tempfile.gettempdir()) / "scicp_c1_test.db"
PASSWORD = "Passw0rd!demo"
USERS = {
    "anh": "anh@example.com", "maria": "maria@bayshuttle.example", "rider": "rider@example.com",
    "iot": "iot@scicp.example", "staff": "staff@scicp.example", "admin": "admin@scicp.example",
}


def test_settings(**overrides) -> Settings:
    base = dict(database_url=os.environ.get("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{TEST_DB}"),
                mongo_url="mock", jwt_secret="test-jwt-secret-0123456789abcdef0123456789",
                service_token_secret="test-service-secret-0123456789abcdef01234",
                mail_mode="log", seed_on_start=True, stubs_inline=False, login_rate_limit_per_minute=100000,
                c2_base_url="http://c2.invalid/api/v1", c3_base_url="http://c3.invalid/api/v1", log_level="WARNING")
    base.update(overrides)
    return Settings(_env_file=None, **base)


# -- fakes --------------------------------------------------------------------------------------
SEED_VEHICLES = {
    1: [("AV-01", "1HGCM82633A004352", "Anh's Civic", "ACTIVE", "ONLINE")],
    2: [("AV-10", "5YJ3E1EA7KF300010", "Shuttle 1", "ACTIVE", "ONLINE"),
        ("AV-11", "5YJ3E1EA7KF300011", "Shuttle 2", "ACTIVE", "ONLINE"),
        ("AV-12", "5YJ3E1EA7KF300012", "Shuttle 3", "ACTIVE", "ONLINE"),
        ("AV-13", "5YJ3E1EA7KF300013", "Shuttle 4", "ACTIVE", "OFFLINE"),
        ("AV-14", "5YJ3E1EA7KF300014", "Shuttle 5", "REGISTERED", "NEVER")],
}


class FakeVehicleClient:
    def __init__(self) -> None:
        self.fail = False
        self.calls = 0

    async def list(self, tenant_id: int) -> List[Dict[str, Any]]:
        self.calls += 1
        if self.fail:
            raise ClientError("C2 unreachable")
        return [{"vehicleId": code, "vin": vin, "label": label, "make": "Tesla", "model": "Model 3", "year": 2023,
                 "status": status, "connection": conn, "lastSeenAt": "2026-10-30T12:00:00Z", "retentionDays": 90,
                 "deviceSummary": {"total": 2, "online": 2 if conn == "ONLINE" else 0,
                                   "offline": 2 if conn == "OFFLINE" else 0, "fault": 0, "disabled": 0}}
                for code, vin, label, status, conn in SEED_VEHICLES.get(tenant_id, [])]

    async def device_summary(self, vehicle_id: str, tenant_id: Optional[int] = None):
        return [{"deviceId": "dev-0111", "type": "MIC_INTERIOR", "status": "ONLINE", "lastSeenAt": None}]


class FakeAlertClient:
    def __init__(self) -> None:
        self.fail = False
        self.alerts = {
            1: [{"alertId": 20482, "vehicleId": "AV-01", "type": "ACCIDENT", "severity": "CRITICAL", "status": "NEW",
                 "topClass": "crash", "confidence": 0.91, "createdAt": "2026-11-04T21:42:12Z", "ageSeconds": 60,
                 "acknowledgedBy": None, "lat": 37.3352, "lon": -121.8811}],
            2: [{"alertId": 20470, "vehicleId": "AV-12", "type": "SECURITY", "severity": "HIGH", "status": "NEW",
                 "topClass": "glass_break", "confidence": 0.8, "createdAt": "2026-10-30T10:00:00Z", "ageSeconds": 600,
                 "acknowledgedBy": None, "lat": 37.336, "lon": -121.88}],
        }

    async def open_alerts(self, tenant_id: int, limit: int = 10) -> Tuple[List[Dict[str, Any]], int]:
        if self.fail:
            raise ClientError("C3 unreachable")
        items = self.alerts.get(tenant_id, [])
        return items[:limit], len(items)

    async def get_alert(self, alert_id: int, tenant_id: Optional[int] = None):
        for items in self.alerts.values():
            for a in items:
                if a["alertId"] == alert_id:
                    return a
        raise ClientError("C3 answered 404")

    async def map_points(self, tenant_id: int) -> List[Dict[str, Any]]:
        if self.fail:
            raise ClientError("C3 unreachable")
        return [{k: a[k] for k in ("alertId", "vehicleId", "lat", "lon", "severity", "type", "createdAt")}
                for a in self.alerts.get(tenant_id, [])]


class FakeStorageClient:
    """In-memory bucket with the same interface as StorageClient."""

    bucket = "scicp-models"

    def __init__(self) -> None:
        self.objects: Dict[str, bytes] = {}

    @staticmethod
    def artifact_key(model_id: int, version: str) -> str:
        return f"models/{model_id}/{version}/model.pt"

    def artifact_uri(self, model_id: int, version: str) -> str:
        return f"s3://{self.bucket}/{self.artifact_key(model_id, version)}"

    async def presign_put(self, key: str) -> str:
        return f"https://fake-s3.local/{self.bucket}/{key}?X-Amz-Signature=put"

    async def presign_get(self, key: str) -> str:
        return f"https://fake-s3.local/{self.bucket}/{key}?X-Amz-Signature=get"

    async def head(self, key: str) -> Optional[HeadInfo]:
        data = self.objects.get(key)
        if data is None:
            return None
        return HeadInfo(size=len(data), sha256=hashlib.sha256(data).hexdigest())

    async def sha256_of(self, key: str) -> str:
        return hashlib.sha256(self.objects[key]).hexdigest()

    async def verify(self, key: str, size_bytes, sha256) -> bool:
        head = await self.head(key)
        return head is not None and size_bytes is not None and head.size == int(size_bytes) and bool(sha256) \
            and head.sha256 == sha256.lower()

    async def put_bytes(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    async def ensure_bucket(self) -> bool:
        return True


def install_fakes(container) -> None:
    from seed.seed import ARTIFACTS
    container.vehicle_client = FakeVehicleClient()
    container.alert_client = FakeAlertClient()
    storage = FakeStorageClient()
    for version, data in ARTIFACTS.items():
        storage.objects[storage.artifact_key(1, version)] = data
    container.storage = storage


# -- fixtures ---------------------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="session")
async def app():
    if TEST_DB.exists():
        TEST_DB.unlink()
    application = create_app(test_settings())
    install_fakes(application.state.container)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture(scope="session")
def container(app):
    return app.state.container


@pytest_asyncio.fixture(scope="session")
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


@pytest.fixture(autouse=True)
def reset_fakes(container):
    """Every test starts with healthy neighbors and cold caches."""
    container.vehicle_client.fail = False
    container.alert_client.fail = False
    container.overview_cache.clear()
    container.map_cache.clear()
    container.pins_cache.clear()
    yield


@pytest_asyncio.fixture
async def db(container):
    async with container.session_factory() as session:
        yield session


class Auth:
    def __init__(self, client: AsyncClient) -> None:
        self.client = client
        self._tokens: Dict[str, str] = {}

    async def login(self, who: str, password: str = PASSWORD):
        email = USERS.get(who, who)
        response = await self.client.post("/api/v1/auth/login", json={"email": email, "password": password})
        return response

    async def token(self, who: str) -> str:
        if who not in self._tokens:
            response = await self.login(who)
            assert response.status_code == 200, response.text
            self._tokens[who] = response.json()["accessToken"]
        return self._tokens[who]

    async def headers(self, who: str) -> Dict[str, str]:
        return {"Authorization": f"Bearer {await self.token(who)}"}


@pytest_asyncio.fixture(scope="session")
async def auth(client):
    return Auth(client)


@pytest_asyncio.fixture(scope="session")
async def service_headers(container):
    return {"Authorization": f"Bearer {container.tokens.issue_service_token('test')}"}
