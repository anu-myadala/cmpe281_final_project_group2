"""Standalone C2 and C3 stub servers (the `stubs` container of docker compose):

    python -m stubs.standalone

Two FastAPI apps in one process: C2 on :8002 and C3 on :8003, each serving
its router under /api/v1 with the platform error envelope, sharing one
MySQL engine and one MongoDB client. Events reach C1 over HTTP
(POST {C1_BASE_URL}/internal/events with the service token); the demo
generator runs here too.

Environment (same names as the C1 API): DATABASE_URL, MONGO_URL,
JWT_SECRET, SERVICE_TOKEN_SECRET, C1_BASE_URL (default
http://api:8000/api/v1), STUBS_C2_PORT (8002), STUBS_C3_PORT (8003),
STUBS_HOST (0.0.0.0), LOG_LEVEL, and the DEMO_* knobs of stubs.generator.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI

_API_DIR = Path(__file__).resolve().parents[1] / "c1" / "api"
try:
    import app  # noqa: F401
except ImportError:
    sys.path.insert(0, str(_API_DIR))

from app.config import Settings  # noqa: E402
from app.db import make_engine, make_mongo, make_session_factory  # noqa: E402
from app.errors import install_error_handlers  # noqa: E402

from .c2 import c2_router  # noqa: E402
from .c3 import c3_router  # noqa: E402
from .common import HttpPublisher, StubContext  # noqa: E402
from .generator import demo_generator  # noqa: E402

log = logging.getLogger("scicp.stubs")


@contextlib.contextmanager
def _no_signal_capture():
    yield


def build_apps(settings: Settings) -> tuple:
    """(c2_app, c3_app, ctx): both apps share the context; the generator uses the same publisher."""
    engine = make_engine(settings.database_url)
    mongo = make_mongo(settings.mongo_url, settings.mongo_database_name)
    c1_base = os.environ.get("C1_BASE_URL", "http://api:8000/api/v1")
    ctx = StubContext(make_session_factory(engine), mongo, settings.jwt_secret, settings.service_token_secret,
                      HttpPublisher(c1_base, settings.service_token_secret, settings.client_timeout_seconds))

    def make(name: str, router, port: int) -> FastAPI:
        application = FastAPI(title=f"SCICP {name} stub", version="1.0.0", docs_url="/api/v1/docs",
                              openapi_url="/api/v1/openapi.json")
        application.state.stub_ctx = ctx
        application.state.engine = engine
        install_error_handlers(application)
        application.include_router(router, prefix="/api/v1")

        @application.get("/api/v1/health")
        async def health():
            return {"status": "ok", "component": name.lower(), "stub": True, "port": port, "c1": c1_base}

        return application

    c2_port = int(os.environ.get("STUBS_C2_PORT", "8002"))
    c3_port = int(os.environ.get("STUBS_C3_PORT", "8003"))
    return make("C2", c2_router, c2_port), make("C3", c3_router, c3_port), ctx, engine, mongo


async def serve() -> None:
    settings = Settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    c2_app, c3_app, ctx, engine, mongo = build_apps(settings)
    host = os.environ.get("STUBS_HOST", "0.0.0.0")
    c2_port = int(os.environ.get("STUBS_C2_PORT", "8002"))
    c3_port = int(os.environ.get("STUBS_C3_PORT", "8003"))

    @asynccontextmanager
    async def generator_task():
        task = asyncio.create_task(demo_generator(ctx.publisher, mongo, settings.database_url))
        try:
            yield task
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    servers = [uvicorn.Server(uvicorn.Config(c2_app, host=host, port=c2_port, log_level=settings.log_level.lower(),
                                             proxy_headers=True, forwarded_allow_ips="*")),
               uvicorn.Server(uvicorn.Config(c3_app, host=host, port=c3_port, log_level=settings.log_level.lower(),
                                             proxy_headers=True, forwarded_allow_ips="*"))]
    # One handler stops both servers: uvicorn's per-server capture_signals would otherwise compete.
    for server in servers:
        server.capture_signals = _no_signal_capture  # type: ignore[method-assign]

    def _exit(sig, frame) -> None:
        for server in servers:
            server.handle_exit(sig, frame)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _exit)
    log.info("C2 stub on %s:%d, C3 stub on %s:%d, C1 at %s", host, c2_port, host, c3_port, ctx.publisher.base_url)
    async with generator_task():
        try:
            await asyncio.gather(*(s.serve() for s in servers))
        finally:
            await engine.dispose()


def main() -> None:
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
