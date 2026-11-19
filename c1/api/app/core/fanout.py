"""MqttFanout: shares the EventBus between the API replicas (EVENT_FANOUT=1).

Every replica keeps one MQTT connection, subscribes to scicp/v1/platform/c1-events and
forwards each event it publishes there, tagged with its instance id. The other replicas
deliver the event to their own subscribers (WebSocket rooms, cache expiry); the sender
ignores its own copy because it delivered it already. So a browser sees an alert live
whichever replica its WebSocket landed on, and whichever replica C3 called.

If the broker is down, forward() logs and drops: the local delivery still happened and the
pages of the other replicas catch up on their next refresh (the overview and map refetch).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from ..config import Settings
from .eventbus import EventBus

log = logging.getLogger("scicp.fanout")

FANOUT_TOPIC = "scicp/v1/platform/c1-events"


def instance_id() -> str:
    """The container's host name (unique per replica) and the process id."""
    return f"{socket.gethostname()}-{os.getpid()}"


def encode(origin: str, topic: str, payload: Dict[str, Any]) -> str:
    return json.dumps({"origin": origin, "topic": topic, "payload": payload}, default=str)


def decode(raw: bytes, own_origin: str) -> Optional[tuple]:
    """(topic, payload) of an event from another replica; None for our own copy or a bad message."""
    try:
        message = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(message, dict) or message.get("origin") == own_origin:
        return None
    topic, payload = message.get("topic"), message.get("payload")
    if not isinstance(topic, str) or not isinstance(payload, dict):
        return None
    return topic, payload


class MqttFanout:
    def __init__(self, settings: Settings, bus: EventBus, origin: Optional[str] = None) -> None:
        self.settings = settings
        self.bus = bus
        self.origin = origin or instance_id()
        self.client: Any = None
        self.connected = False
        self.received = 0
        self.forwarded = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self) -> None:
        import paho.mqtt.client as mqtt
        self._loop = asyncio.get_running_loop()
        parsed = urlparse(self.settings.mqtt_url or "")
        host = parsed.hostname or "localhost"
        port = parsed.port or (8883 if parsed.scheme in ("mqtts", "ssl") else 1883)
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"scicp-c1-{self.origin}"[:64])
        if self.settings.mqtt_username:
            client.username_pw_set(self.settings.mqtt_username, self.settings.mqtt_password)
        if parsed.scheme in ("mqtts", "ssl"):
            client.tls_set()

        def on_connect(cli, _ud, _flags, reason_code, _props=None):
            if reason_code.is_failure:
                log.warning("event fan-out: MQTT connect refused (%s)", reason_code)
                return
            self.connected = True
            cli.subscribe(FANOUT_TOPIC, qos=1)
            log.info("event fan-out on %s:%s as %s", host, port, self.origin)

        def on_disconnect(_cli, _ud, _flags, reason_code, _props=None):
            self.connected = False
            log.warning("event fan-out: MQTT disconnected (%s); reconnecting", reason_code)

        def on_message(_cli, _ud, msg):
            event = decode(msg.payload, self.origin)
            if event is None or self._loop is None:
                return
            self.received += 1
            self._loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.bus.deliver(*event)))

        client.on_connect, client.on_disconnect, client.on_message = on_connect, on_disconnect, on_message
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.connect_async(host, port, keepalive=30)
        client.loop_start()
        self.client = client
        self.bus.forwarder = self.forward

    def forward(self, topic: str, payload: Dict[str, Any]) -> bool:
        if self.client is None or not self.connected:
            log.warning("event fan-out: broker not connected, %s stays on this replica", topic)
            return False
        self.client.publish(FANOUT_TOPIC, encode(self.origin, topic, payload), qos=1)
        self.forwarded += 1
        return True

    def stop(self) -> None:
        self.bus.forwarder = None
        if self.client is not None:
            self.client.loop_stop()
            try:
                self.client.disconnect()
            except Exception:  # noqa: BLE001
                pass
