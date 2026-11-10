"""MQTT input (paho-mqtt 2.1.0): audio events into the priority queue, model activations to the ModelManager.

  scicp/v1/{tenantId}/{vehicleId}/audio/event   QoS 1; tenant and vehicle are taken from the TOPIC
  scicp/v1/platform/model/activated             QoS 1, retained; wakes the ModelManager

manual_ack=True: an event's PUBACK goes out only when the pipeline has stored it (Pipeline._ack).
clean_session=False with a fixed client id: the broker keeps the subscription and the unacked
messages while the worker restarts. Malformed messages are acknowledged at once and counted.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable, Optional, Tuple

import paho.mqtt.client as mqtt

from .config import EVENT_TOPIC, MODEL_ACTIVATED_TOPIC, Settings
from .prio_queue import PriorityEventQueue, QueuedEvent

log = logging.getLogger("c4.mqtt")


def parse_topic(topic: str) -> Optional[Tuple[str, str]]:
    """'scicp/v1/tA/AV-01/audio/event' -> ('tA', 'AV-01'); anything else -> None."""
    parts = topic.split("/")
    if len(parts) == 6 and parts[:2] == ["scicp", "v1"] and parts[4:] == ["audio", "event"] and parts[2] and parts[3]:
        return parts[2], parts[3]
    return None


class MqttIn:
    def __init__(self, settings: Settings, queue: PriorityEventQueue, on_activated: Callable[[str], None]) -> None:
        self.s, self.queue, self.on_activated = settings, queue, on_activated
        self.malformed = 0
        self.client: Optional[mqtt.Client] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # -- runs on the event loop ----------------------------------------------------------------------
    def enqueue(self, item: QueuedEvent) -> None:
        dropped = self.queue.put(item)
        if dropped is not None:
            log.warning("queue full (%d): dropped %s event %s (%d dropped so far)", self.queue.maxsize,
                        "HIGH" if dropped.high else "NORMAL", dropped.payload.get("eventId"), self.queue.dropped)
            self.ack(dropped.mid)

    def ack(self, mid: Optional[int]) -> None:
        if mid is not None and self.client is not None:
            self.client.ack(mid, 1)

    # -- runs on paho's network thread ---------------------------------------------------------------
    def handle(self, topic: str, payload: bytes, mid: Optional[int], retain: bool = False) -> Optional[QueuedEvent]:
        if topic == MODEL_ACTIVATED_TOPIC:
            self._ack_now(mid)
            try:
                version = (json.loads(payload.decode() or "{}") or {}).get("version")
            except (ValueError, AttributeError):
                version = None
            self.on_activated(f"MQTT {topic} {version}{' (retained)' if retain else ''}")
            return None
        where = parse_topic(topic)
        try:
            event = json.loads(payload)
        except ValueError:
            event = None
        if where is None or not isinstance(event, dict):
            self.malformed += 1
            self._ack_now(mid)
            log.warning("ignored a malformed message on %s", topic)
            return None
        item = QueuedEvent.build(where[0], where[1], event, mid, time.time())
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.enqueue, item)
        else:
            self.enqueue(item)
        return item

    def _ack_now(self, mid: Optional[int]) -> None:
        if mid is None or self.client is None:
            return
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.ack, mid)
        else:
            self.ack(mid)

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"scicp-{self.s.worker_id}",
                             clean_session=False, manual_ack=True)

        def on_connect(cli, _ud, _flags, reason_code, _props=None):
            if reason_code.is_failure:
                log.warning("MQTT connect refused (%s)", reason_code)
                return
            cli.subscribe([(EVENT_TOPIC, 1), (MODEL_ACTIVATED_TOPIC, 1)])
            log.info("connected to %s:%d, subscribed to %s and %s", self.s.mqtt_host, self.s.mqtt_port,
                     EVENT_TOPIC, MODEL_ACTIVATED_TOPIC)

        def on_disconnect(_cli, _ud, _flags, reason_code, _props=None):
            log.warning("MQTT disconnected (%s); reconnecting", reason_code)

        def on_message(_cli, _ud, msg):
            self.handle(msg.topic, msg.payload, msg.mid, msg.retain)

        client.on_connect, client.on_disconnect, client.on_message = on_connect, on_disconnect, on_message
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.connect_async(self.s.mqtt_host, self.s.mqtt_port, keepalive=30)  # anonymous broker
        client.loop_start()
        self.client = client

    def stop(self) -> None:
        if self.client is not None:
            self.client.loop_stop()
            try:
                self.client.disconnect()
            except Exception:  # noqa: BLE001
                pass
