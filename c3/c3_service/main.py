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
from .routers import alerts
from .schema import create_c3_schema
from .timeutil import iso, utcnow

log = logging.getLogger("scicp.c3")


def create_app(settings: Optional[Settings] = None, mongo: Any = None) -> FastAPI:
    """`mongo` lets the tests hand in the same mock database the seed wrote to."""
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
        log.info("C3 ready: %s, MongoDB %s", engine.dialect.name, settings.mongo_database_name)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="SCICP C3 Alert Tracking and Monitoring", version="1.0.0", lifespan=lifespan,
                  docs_url=f"{settings.api_prefix}/docs", openapi_url=f"{settings.api_prefix}/openapi.json")
    app.state.settings = settings
    install_error_handlers(app)
    app.include_router(alerts.router, prefix=settings.api_prefix)

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
