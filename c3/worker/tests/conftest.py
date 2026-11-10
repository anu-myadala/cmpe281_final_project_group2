import sys
from pathlib import Path
from typing import List

import pytest
from mongomock_motor import AsyncMongoMockClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # c4_worker
sys.path.insert(0, str(Path(__file__).resolve().parent))           # fakes

from c4_worker.clients import C1Client  # noqa: E402
from c4_worker.model_manager import ModelManager  # noqa: E402
from c4_worker.pipeline import Pipeline  # noqa: E402
from c4_worker.store import ensure_indexes  # noqa: E402
from c4_worker.tokens import ServiceToken  # noqa: E402
import fakes  # noqa: E402


@pytest.fixture
def services():
    return fakes.FakeServices()


@pytest.fixture
async def http(services):
    async with services.client() as client:
        yield client


@pytest.fixture
async def db():
    database = AsyncMongoMockClient()["scicp"]
    await ensure_indexes(database)
    return database


@pytest.fixture
def waits() -> List[float]:
    return []


@pytest.fixture
def sleep(waits):
    async def _sleep(s: float) -> None:
        waits.append(s)
    return _sleep


@pytest.fixture
def token():
    return ServiceToken(fakes.SECRET, "c4-worker")


@pytest.fixture
def encoder():
    return fakes.FakeEncoder("crash")


@pytest.fixture
def manager(http, encoder, token, tmp_path):
    return ModelManager(C1Client(fakes.C1, token, "c4-worker-1", "sound-classifier"), encoder,
                        tmp_path / "cache", http)


@pytest.fixture
def acks():
    return []


@pytest.fixture
def pipeline(http, db, manager, token, encoder, sleep, acks, services):
    def ack(mid):
        acks.append(mid)
        services.log.append(f"ack {mid}")
    return Pipeline(fakes.settings(), http, db, manager, token, encoder, sleep=sleep, ack=ack)
