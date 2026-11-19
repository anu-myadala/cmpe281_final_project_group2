"""Running C2 as several replicas behind the load balancer: shared MQTT subscriptions, unique client
ids, and the named lock that keeps the timers to one replica at a time."""
from __future__ import annotations

import asyncio

from c2_service.db import make_engine, named_lock
from c2_service.services.mqtt import TOPIC_STATUS, TOPIC_TELEMETRY, client_id, subscription


def test_shared_subscription_only_with_a_group():
    assert subscription(TOPIC_TELEMETRY, "") == "scicp/v1/+/+/telemetry"
    assert subscription(TOPIC_STATUS, "c2") == "$share/c2/scicp/v1/+/+/status"


def test_client_id_is_unique_per_host():
    ident = client_id("c2-api")
    assert ident.startswith("c2-api-") and len(ident) > len("c2-api-") and len(ident) <= 64


def test_named_lock_is_a_no_op_on_sqlite():
    async def run():
        engine = make_engine("sqlite+aiosqlite:///:memory:")
        async with named_lock(engine, "scicp-c2-test") as mine:
            assert mine is True
        await engine.dispose()
    asyncio.run(run())
