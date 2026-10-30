"""MqttPublisher: publishes platform messages to the Mosquitto broker
(scicp/v1/platform/model/activated, QoS 1, retained). Optional at runtime:
without MQTT_URL every publish is logged and skipped."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from ..config import Settings

log = logging.getLogger("scicp.mqtt")

MODEL_ACTIVATED_TOPIC = "scicp/v1/platform/model/activated"


class MqttPublisher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.published: list = []

    @property
    def enabled(self) -> bool:
        return bool(self.settings.mqtt_url)

    async def publish(self, topic: str, payload: Dict[str, Any], qos: int = 1, retain: bool = False) -> bool:
        self.published.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})
        if not self.enabled:
            log.info("MQTT disabled (MQTT_URL unset): skipped %s %s", topic, payload)
            return False
        try:
            await asyncio.to_thread(self._publish_sync, topic, json.dumps(payload), qos, retain)
            return True
        except Exception as exc:  # noqa: BLE001 - the worker also polls the ACTIVE row every 30 s
            log.error("MQTT publish to %s failed: %s", topic, exc)
            return False

    def _publish_sync(self, topic: str, payload: str, qos: int, retain: bool) -> None:
        import paho.mqtt.client as mqtt
        parsed = urlparse(self.settings.mqtt_url or "")
        host = parsed.hostname or "localhost"
        port = parsed.port or (8883 if parsed.scheme in ("mqtts", "ssl") else 1883)
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="scicp-c1-api")
        if self.settings.mqtt_username:
            client.username_pw_set(self.settings.mqtt_username, self.settings.mqtt_password)
        if parsed.scheme in ("mqtts", "ssl"):
            client.tls_set()
        client.connect(host, port, keepalive=10)
        info = client.publish(topic, payload, qos=qos, retain=retain)
        info.wait_for_publish(timeout=5)
        client.disconnect()
