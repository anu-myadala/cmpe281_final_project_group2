"""FastAPI application factory of component C1 (SCICP Cloud Platform
Dashboard and System Admin, with the C4 model registry pages).

    uvicorn app.main:app --reload

Developer mode (one process is the whole demo): MONGO_URL=mock,
STUBS_INLINE=1 (mounts the C2/C3 stubs under /c2/api/v1 and /c3/api/v1) and
SEED_ON_START=1 (runs seed/seed.py in process at startup).
"""
from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from .config import Settings, get_settings
from .container import AppContainer
from .db import create_schema
from .errors import install_error_handlers
from .models import Tenant
from .routers import admin, auth, dashboard, internal, models, reports, storage, subscriptions, ws

log = logging.getLogger("scicp")

REPO_ROOT = Path(__file__).resolve().parents[3]  # <repo>/c1/api/app/main.py -> <repo>


def _ensure_repo_root_on_path() -> None:
    """seed/ and stubs/ live at the repository root next to c1/."""
    root = str(REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def stubs_mount(app: FastAPI, container: AppContainer) -> Optional[object]:
    """Mount the inline C2/C3 stubs when STUBS_INLINE=1 and stubs.app is importable.

    stubs.app exposes c2_router and c3_router (APIRouters) and the coroutine
    demo_generator(eventbus, mongo_db, mysql_url). Returns the generator
    coroutine function (started by the lifespan) or None.
    """
    if not container.settings.stubs_inline:
        return None
    _ensure_repo_root_on_path()
    try:
        from stubs.app import c2_router, c3_router, demo_generator  # type: ignore
    except ImportError as exc:
        log.warning("STUBS_INLINE=1 but stubs.app is not importable (%s); running without stubs", exc)
        return None
    app.include_router(c2_router, prefix="/c2/api/v1")
    app.include_router(c3_router, prefix="/c3/api/v1")
    log.info("Inline C2/C3 stubs mounted at /c2/api/v1 and /c3/api/v1")
    return demo_generator


async def run_seed(container: AppContainer) -> None:
    """Import seed/seed.py as a module and run it against this process's databases."""
    _ensure_repo_root_on_path()
    try:
        from seed import seed as seed_module  # type: ignore
    except ImportError as exc:
        log.warning("SEED_ON_START=1 but seed.seed is not importable (%s)", exc)
        return
    await seed_module.run(container.engine, container.mongo, storage=container.storage)


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # One INFO line per outgoing request (C2/C3 calls, the inline worker) drowns the useful logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    container = AppContainer.build(settings)

    async def resolve_tenant_code(code: str) -> Optional[int]:
        """C2/C3 may route events with the tenant code (tA); the hub needs the numeric id."""
        async with container.session_factory() as session:
            return (await session.execute(select(Tenant.tenant_id).where(Tenant.code == code))).scalar_one_or_none()

    container.hub.tenant_resolver = resolve_tenant_code

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await create_schema(container.engine)
        if settings.seed_on_start:
            await run_seed(container)
        tasks = []
        generator = getattr(app.state, "demo_generator", None)
        if generator is not None:
            tasks.append(asyncio.create_task(generator(container.bus, container.mongo, settings.database_url)))
            log.info("Demo event generator started")
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await container.engine.dispose()

    app = FastAPI(title="SCICP C1 API", version="1.0.0", lifespan=lifespan,
                  docs_url=f"{settings.api_prefix}/docs", openapi_url=f"{settings.api_prefix}/openapi.json")
    app.state.container = container
    app.state.settings = settings
    install_error_handlers(app)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    prefix = settings.api_prefix
    for router in (auth.router, dashboard.router, subscriptions.router, reports.router, admin.router,
                   models.router, internal.router, storage.router):
        app.include_router(router, prefix=prefix)
    app.include_router(ws.router)            # /ws (what nginx proxies)
    app.include_router(ws.router, prefix=prefix)  # /api/v1/ws as an alias

    @app.get(f"{prefix}/health", tags=["health"])
    async def health():
        return {"status": "ok", "component": "c1", "mongo": "mock" if settings.mongo_url == "mock" else "live",
                "stubsInline": bool(getattr(app.state, "demo_generator", None))}

    app.state.demo_generator = stubs_mount(app, container)
    return app


app = create_app()
