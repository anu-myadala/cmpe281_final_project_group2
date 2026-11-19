"""MqttGateway: C2's one MQTT client (Section 2.8 of the C2 design).

It subscribes to the telemetry and status topics of every car and dispatches them, and it
publishes config and command messages. It reconnects with back-off 1 to 30 s; while it is
disconnected publish() answers False, so a command simply stays PENDING (BR-C2-13).
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
from typing import Any, Optional

log = logging.getLogger("scicp.c2.mqtt")

TOPIC_TELEMETRY = "scicp/v1/+/+/telemetry"
TOPIC_STATUS = "scicp/v1/+/+/status"


def subscription(topic: str, share_group: str) -> str:
    """The topic filter to subscribe with: shared ($share/<group>/<topic>) when C2 runs as several
    replicas, so that the broker delivers each message to one replica of the group only."""
    return f"$share/{share_group}/{topic}" if share_group else topic


def client_id(base: str) -> str:
    """MQTT client ids must be unique: a second client with the same id would disconnect the first."""
    return f"{base}-{socket.gethostname()}"[:64]


class MqttGateway:
    def __init__(self, settings: Any, telemetry: Any, tracker: Any) -> None:
        self.settings = settings
        self.telemetry = telemetry
        self.tracker = tracker
        self._client = None
        self._task: Optional[asyncio.Task] = None
        self.connected = asyncio.Event()

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="c2-mqtt")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def publish(self, topic: str, payload: dict, qos: int = 1) -> bool:
        client = self._client
        if client is None or not self.connected.is_set():
            log.info("MQTT not connected: %s kept for later", topic)
            return False
        try:
            await client.publish(topic, json.dumps(payload, default=str), qos=qos)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("MQTT publish to %s failed: %s", topic, exc)
            return False

    async def _run(self) -> None:
        import aiomqtt
        host, port = self.settings.mqtt_host_port
        delay = 1.0
        while True:
            try:
                async with aiomqtt.Client(hostname=host, port=port, identifier=client_id(self.settings.c2_mqtt_client_id),
                                          username=self.settings.mqtt_username or None,
                                          password=self.settings.mqtt_password or None, keepalive=30) as client:
                    self._client = client
                    group = self.settings.c2_mqtt_share_group
                    await client.subscribe(subscription(TOPIC_TELEMETRY, group), qos=0)
                    await client.subscribe(subscription(TOPIC_STATUS, group), qos=1)
                    self.connected.set()
                    delay = 1.0
                    log.info("MQTT connected to %s:%s; listening to telemetry and status%s", host, port,
                             f" (shared group {group})" if group else "")
                    async for message in client.messages:
                        await self._dispatch(str(message.topic), message.payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("MQTT connection to %s:%s lost or refused: %s; retrying in %.0f s", host, port, exc, delay)
            finally:
                self.connected.clear()
                self._client = None
            await asyncio.sleep(delay)
            delay = min(30.0, delay * 2)

    async def _dispatch(self, topic: str, raw: bytes) -> None:
        parts = topic.split("/")
        if len(parts) != 5 or parts[0] != "scicp" or parts[1] != "v1":
            return
        _, _, tenant_code, vehicle_code, kind = parts
        try:
            msg = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            log.warning("bad JSON on %s ignored", topic)
            return
        if not isinstance(msg, dict):
            return
        try:
            if kind == "telemetry":
                await self.telemetry.on_telemetry(tenant_code, vehicle_code, msg)
            elif kind == "status":
                await self.tracker.on_status(tenant_code, vehicle_code, msg)
        except Exception as exc:  # noqa: BLE001 - one bad message must not stop the listener
            log.exception("handling %s failed: %s", topic, exc)
