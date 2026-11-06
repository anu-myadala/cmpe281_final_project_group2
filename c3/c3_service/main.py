"""FastAPI application of C3. Run it with

    uvicorn c3_service.main:app --port 8003

create_app() builds the app. The lifespan opens the MySQL engine and the MongoDB
client once, creates and seeds C3's own tables (schema.py), and keeps the handles
on app.state for the request dependencies. Every route lives under /api/v1 (nginx
serves them to the browser as /c3/api/v1/...).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, Request
from sqlalchemy import text

from .auth import TenantDirectory
from .config import Settings, get_settings
from .db import make_engine, make_mongo, make_session_factory
from .errors import install_error_handlers
from .routers import alerts, internal
from .schema import create_c3_schema
from .services.alert_mailer import AlertMailer
from .services.c1_client import C1Client
from .services.notification_service import NotificationService
from .services.publisher import HttpPublisher, NoopPublisher
from .timeutil import iso, utcnow

log = logging.getLogger("scicp.c3")


def make_publisher(settings: Settings):
    """HttpPublisher to C1_BASE_URL by default; NoopPublisher with PUBLISHER=noop or without a C1_BASE_URL."""
    if settings.publisher == "noop" or not settings.c1_base_url:
        return NoopPublisher()
    return HttpPublisher(settings.c1_base_url, settings.service_token_secret, settings.publish_timeout_seconds)


def create_app(settings: Optional[Settings] = None, mongo: Any = None, publisher: Any = None,
               c1_client: Optional[C1Client] = None, mailer: Any = None) -> FastAPI:
    """The tests hand in `mongo` (the mock the seed wrote to), a recording `publisher` and a `c1_client`
    on httpx.MockTransport. Without them: the configured MongoDB, make_publisher(settings) and a
    C1Client on C1_BASE_URL."""
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        app.state.tenants = TenantDirectory(app.state.session_factory)
        app.state.mongo = mongo if mongo is not None else make_mongo(settings.mongo_url, settings.mongo_database_name)
        await create_c3_schema(engine)
        app.state.notifier = NotificationService(app.state.session_factory, mailer or AlertMailer(settings),
                                                 settings.retry_waits)
        log.info("C3 ready: %s, MongoDB %s", engine.dialect.name, settings.mongo_database_name)
        try:
            yield
        finally:
            await app.state.notifier.close()  # stops the email retries; their rows stay PENDING
            await engine.dispose()

    app = FastAPI(title="SCICP C3 Alert Tracking and Monitoring", version="1.0.0", lifespan=lifespan,
                  docs_url=f"{settings.api_prefix}/docs", openapi_url=f"{settings.api_prefix}/openapi.json")
    app.state.settings = settings
    app.state.publisher = publisher if publisher is not None else make_publisher(settings)
    app.state.c1_client = c1_client or C1Client(settings.c1_base_url, settings.client_timeout_seconds)
    install_error_handlers(app)
    app.include_router(alerts.router, prefix=settings.api_prefix)
    app.include_router(internal.router, prefix=settings.api_prefix)

    @app.get(f"{settings.api_prefix}/health", tags=["health"])
    async def health(request: Request):
        """200 with the database state; the Docker health check and C1's ops page call it."""
        database = "ok"
        try:
            async with request.app.state.session_factory() as session:
                await session.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            log.warning("health: database check failed: %s", exc)
            database = "down"
        return {"status": "ok", "component": "C3", "database": database, "time": iso(utcnow())}

    return app


app = create_app()
