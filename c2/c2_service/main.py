"""FastAPI application of C2. Run it with

    uvicorn c2_service.main:app --port 8002

The lifespan opens MySQL and MongoDB, creates C2's tables and the seeded cars' devices
(schema.py), builds the services, connects the MQTT gateway, and starts three timers: the
status sweep (10 s), the command timeouts (5 s) and the daily retention job (03:15 UTC).
Every route lives under /api/v1 (nginx serves them to the browser as /c2/api/v1/...); the
C2 console page is served at /console/ (http://localhost:8080/c2/console/).
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .auth import TenantDirectory
from .config import Settings, get_settings
from .db import make_engine, make_mongo, make_session_factory
from .errors import install_error_handlers
from .routers import clips, devices, vehicles
from .schema import create_c2_schema
from .services.clip_store import make_clip_store
from .services.clips import ClipService
from .services.commands import CommandService
from .services.mqtt import MqttGateway
from .services.publisher import HttpPublisher, NoopPublisher
from .services.registry import NoBrokerAdmin, SecretBox
from .services.status import StatusTracker, TelemetryIngestor
from .services.support import C1Client
from .timeutil import iso, utcnow

log = logging.getLogger("scicp.c2")
CONSOLE_DIR = Path(__file__).resolve().parent / "console"


@dataclass
class Services:
    settings: Settings
    tenants: TenantDirectory
    publisher: Any
    tracker: StatusTracker
    telemetry: TelemetryIngestor
    commands: CommandService
    clips: ClipService
    broker: Any
    box: SecretBox
    c1: C1Client
    gateway: Any


class OfflineGateway:
    """C2_MQTT_ENABLED=0 (tests): nothing is sent, commands stay PENDING."""
    connected = asyncio.Event()

    async def publish(self, topic: str, payload: dict, qos: int = 1) -> bool:
        return False

    def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


async def _every(seconds: float, what, name: str) -> None:
    while True:
        await asyncio.sleep(seconds)
        try:
            await what()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("%s failed: %s", name, exc)


async def _daily(hour: int, minute: int, what) -> None:
    while True:
        now = utcnow()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            await what()
        except Exception as exc:  # noqa: BLE001
            log.warning("retention job failed: %s", exc)


class _QuietPolling(logging.Filter):
    """Drop access lines for successful GETs (the console polls every 3 s); keep writes and errors."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args if isinstance(record.args, tuple) else ()
        if len(args) >= 5:
            method, status = args[1], args[4]
            if method in ("GET", "HEAD") and isinstance(status, int) and status < 400:
                return False
        return True


def create_app(settings: Optional[Settings] = None, mongo: Any = None, publisher: Any = None,
               gateway: Any = None, c1_transport: Any = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not any(isinstance(f, _QuietPolling) for f in logging.getLogger("uvicorn.access").filters):
        logging.getLogger("uvicorn.access").addFilter(_QuietPolling())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        session_factory = make_session_factory(engine)
        db = mongo if mongo is not None else make_mongo(settings.mongo_url, settings.mongo_database_name)
        tenants = TenantDirectory(session_factory)
        await create_c2_schema(engine, db, settings.c2_offline_after_s)
        pub = publisher if publisher is not None else (
            NoopPublisher() if settings.publisher == "noop" or not settings.c1_base_url
            else HttpPublisher(settings.c1_base_url, settings.service_token_secret, 2.0))
        tracker = StatusTracker(session_factory, db, pub, tenants, settings)
        telemetry = TelemetryIngestor(db, tracker)
        gw = gateway if gateway is not None else (MqttGateway(settings, telemetry, tracker)
                                                  if settings.c2_mqtt_enabled else OfflineGateway())
        box = SecretBox(settings.network_secret or f"{settings.service_token_secret}:c2-network")
        commands = CommandService(session_factory, gw, pub, tenants, settings, box)
        tracker.commands = commands
        token_source = pub.service_token if isinstance(pub, HttpPublisher) else \
            HttpPublisher(settings.c1_base_url, settings.service_token_secret).service_token
        services = Services(settings=settings, tenants=tenants, publisher=pub, tracker=tracker, telemetry=telemetry,
                            commands=commands, clips=ClipService(session_factory, db, make_clip_store(settings), tenants,
                                                                 settings),
                            broker=NoBrokerAdmin(), box=box,
                            c1=C1Client(settings.c1_base_url, token_source, settings.client_timeout_seconds, c1_transport),
                            gateway=gw)
        app.state.engine, app.state.session_factory, app.state.mongo = engine, session_factory, db
        app.state.tenants, app.state.services = tenants, services
        tasks: List[asyncio.Task] = []
        if hasattr(gw, "start"):
            gw.start()
        if settings.c2_background:
            tasks += [asyncio.create_task(_every(settings.c2_sweep_every_s, tracker.sweep, "status sweep")),
                      asyncio.create_task(_every(5.0, commands.timeouts, "command timeouts")),
                      asyncio.create_task(_daily(settings.c2_retention_hour_utc, 15, services.clips.run_retention))]
        log.info("C2 ready: %s, MongoDB %s, MQTT %s, clips %s", engine.dialect.name, settings.mongo_database_name,
                 settings.mqtt_url if settings.c2_mqtt_enabled else "off", settings.c2_clip_backend)
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            if hasattr(gw, "stop"):
                await gw.stop()
            await engine.dispose()

    app = FastAPI(title="SCICP C2 Edge Agent and IoT Device Management", version="1.0.0", lifespan=lifespan,
                  docs_url=f"{settings.api_prefix}/docs", openapi_url=f"{settings.api_prefix}/openapi.json")
    app.state.settings = settings
    install_error_handlers(app)
    app.include_router(vehicles.router, prefix=settings.api_prefix)
    app.include_router(devices.router, prefix=settings.api_prefix)
    app.include_router(clips.router, prefix=settings.api_prefix)
    app.include_router(clips.internal, prefix=settings.api_prefix)

    @app.get(f"{settings.api_prefix}/health", tags=["health"])
    async def health(request: Request):
        database = "ok"
        try:
            async with request.app.state.session_factory() as session:
                await session.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            log.warning("health: database check failed: %s", exc)
            database = "down"
        gw = request.app.state.services.gateway
        mqtt = "connected" if getattr(gw, "connected", None) is not None and gw.connected.is_set() else "disconnected"
        return {"status": "ok", "component": "C2", "database": database, "mqtt": mqtt, "time": iso(utcnow())}

    if CONSOLE_DIR.exists():
        app.mount("/console", StaticFiles(directory=str(CONSOLE_DIR), html=True), name="console")

        @app.get("/", include_in_schema=False)
        async def root():
            return RedirectResponse("console/")

    return app


app = create_app()
