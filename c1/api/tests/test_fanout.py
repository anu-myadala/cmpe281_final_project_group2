"""Several API replicas: the EventBus forwarder, the MQTT envelope of core/fanout.py, and the
X-SCICP-Instance header that shows which replica answered."""
from __future__ import annotations

from app.core.eventbus import EventBus
from app.core.fanout import FANOUT_TOPIC, decode, encode


async def test_publish_delivers_locally_then_forwards_once():
    bus = EventBus()
    seen, forwarded = [], []
    bus.subscribe("alert.created", lambda t, p: seen.append(p["alertId"]))
    bus.forwarder = lambda t, p: forwarded.append((t, p["alertId"]))
    await bus.publish("alert.created", {"alertId": 7})
    assert seen == [7] and forwarded == [("alert.created", 7)]
    await bus.deliver("alert.created", {"alertId": 8})      # an event from another replica
    assert seen == [7, 8] and forwarded == [("alert.created", 7)]  # not forwarded again


async def test_two_replicas_share_an_event_through_the_envelope():
    """Replica A publishes; the broker hands the envelope to A and B; only B delivers it again."""
    a, b = EventBus(), EventBus()
    on_a, on_b, wire = [], [], []
    a.subscribe("*", lambda t, p: on_a.append(t))
    b.subscribe("*", lambda t, p: on_b.append(t))
    a.forwarder = lambda t, p: wire.append(encode("replica-a", t, p))
    await a.publish("alert.updated", {"alertId": 9, "status": "ACKNOWLEDGED"})
    for raw in wire:
        for bus, origin in ((a, "replica-a"), (b, "replica-b")):
            event = decode(raw.encode(), origin)
            if event is not None:
                await bus.deliver(*event)
    assert on_a == ["alert.updated"] and on_b == ["alert.updated"]


def test_bad_envelopes_are_ignored():
    assert decode(b"not json", "x") is None
    assert decode(b'{"origin": "y", "topic": 5, "payload": {}}', "x") is None
    assert decode(encode("x", "alert.created", {}).encode(), "x") is None
    assert FANOUT_TOPIC.startswith("scicp/v1/platform/")


async def test_every_answer_names_the_replica(client, container):
    response = await client.get("/api/v1/health")
    assert response.headers["X-SCICP-Instance"] == container.instance_id
    assert response.json()["instance"] == container.instance_id
