"""The stubs container without its C3 half (used by docker-compose.c3.yml).

Mounted into the stubs image as /srv/stubs_c2_only.py and run instead of
`python -m stubs.standalone`. It reuses the stub's own code unchanged:
stubs.standalone.build_apps() for the C2 app (vehicles, devices, telemetry) and
stubs.generator.demo_generator for the moving cars, the device faults and the
simulated C4 worker. Only the C2 app is served (:8002); the C3 stub app that
build_apps() also returns is never served, because the real C3 runs in its own
container. docker-compose.c3.yml sets DEMO_MAX_ALERTS=0, so the generator creates
no alerts: those come from C3's result call now.
"""
from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

from app.config import Settings
from stubs.generator import demo_generator
from stubs.standalone import build_apps

log = logging.getLogger("scicp.stubs")


async def serve() -> None:
    settings = Settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    c2_app, _c3_stub_not_served, ctx, engine, mongo = build_apps(settings)
    port = int(os.environ.get("STUBS_C2_PORT", "8002"))
    server = uvicorn.Server(uvicorn.Config(c2_app, host=os.environ.get("STUBS_HOST", "0.0.0.0"), port=port,
                                           log_level=settings.log_level.lower(), proxy_headers=True,
                                           forwarded_allow_ips="*"))
    generator = asyncio.create_task(demo_generator(ctx.publisher, mongo, settings.database_url))
    log.info("C2 stub on :%d (C3 is served by the c3 container); DEMO_MAX_ALERTS=%s",
             port, os.environ.get("DEMO_MAX_ALERTS"))
    try:
        await server.serve()  # uvicorn handles SIGINT / SIGTERM
    finally:
        generator.cancel()
        try:
            await generator
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(serve())
