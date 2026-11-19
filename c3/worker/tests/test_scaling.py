"""A pool of C4 workers: audio events through an MQTT shared subscription, one worker per clip;
model activations still reach every worker."""
from __future__ import annotations

import socket

from c4_worker.config import EVENT_TOPIC, MODEL_ACTIVATED_TOPIC, Settings


def test_plain_subscription_by_default():
    assert Settings(service_token_secret="x").event_subscription == EVENT_TOPIC


def test_shared_subscription_for_a_pool():
    settings = Settings(service_token_secret="x", share_group="c4")
    assert settings.event_subscription == "$share/c4/scicp/v1/+/+/audio/event"
    assert not MODEL_ACTIVATED_TOPIC.startswith("$share")


def test_worker_id_auto_uses_the_host_name(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN_SECRET", "x")
    monkeypatch.setenv("WORKER_ID", "auto")
    monkeypatch.setenv("C4_SHARE_GROUP", "c4")
    settings = Settings.from_env()
    assert settings.worker_id == f"c4-{socket.gethostname()}" and settings.share_group == "c4"
