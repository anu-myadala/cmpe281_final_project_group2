"""Entry point: python -m c4_worker.main (the Dockerfile's CMD). Wires MQTT, the model sync, the pipeline and /healthz."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

import httpx
from motor.motor_asyncio import AsyncIOMotorClient

from .clients import C1Client
from .config import Settings
from .encoder import ClapEncoder
from .health import serve_health
from .model_manager import ModelManager
from .mqtt_in import MqttIn
from .pipeline import Pipeline
from .prio_queue import PriorityEventQueue
from .store import ensure_indexes
from .tokens import ServiceToken

log = logging.getLogger("c4")


async def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    s = Settings.from_env()
    token = ServiceToken(s.service_token_secret, s.token_sub)
    encoder = ClapEncoder(s.clap_model)
    db = AsyncIOMotorClient(s.mongo_url, serverSelectionTimeoutMS=5000)[s.mongo_db_name]
    try:
        await ensure_indexes(db)
    except Exception as exc:  # noqa: BLE001  the seed normally created it already
        log.warning("could not ensure the audio_events index: %s", exc)
    queue = PriorityEventQueue(s.queue_size)

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as http:
        manager = ModelManager(C1Client(s.c1_base_url, token, s.worker_id, s.model_name), encoder,
                               Path(s.cache_dir), http)
        mqtt_in = MqttIn(s, queue, manager.wake)
        pipeline = Pipeline(s, http, db, manager, token, encoder, ack=mqtt_in.ack)

        def snapshot():
            return {"status": manager.state, "modelVersion": manager.model_version, "queueDepth": len(queue),
                    "processed": pipeline.processed, "dropped": queue.dropped,
                    "errors": pipeline.errors + mqtt_in.malformed}

        health = await serve_health(snapshot, port=s.health_port)
        log.info("worker %s: C1 %s, C2 %s, C3 %s, MQTT %s:%d, /healthz on :%d", s.worker_id, s.c1_base_url,
                 s.c2_base_url, s.c3_base_url, s.mqtt_host, s.mqtt_port, s.health_port)
        mqtt_in.start()

        async def warm_up():  # CLAP into memory (and into HF_HOME on the first start) before the first clip
            try:
                await asyncio.to_thread(encoder.load)
            except Exception:  # noqa: BLE001
                log.exception("CLAP could not be loaded; it is retried at the first model load or clip")

        tasks = [asyncio.create_task(manager.run(s.poll_s)), asyncio.create_task(pipeline.run(queue)),
                 asyncio.create_task(warm_up())]
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
        log.info("stopping")
        mqtt_in.stop()
        health.close()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
