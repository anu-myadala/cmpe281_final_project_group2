"""DataAccess (C5): the SQLAlchemy async engine, the session factory, the
schema creation at startup and the MongoDB (Motor) client."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .models import Base, V_ALERT_SUMMARY_SQL

log = logging.getLogger("scicp.db")


def make_engine(database_url: str) -> AsyncEngine:
    kwargs: dict = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        kwargs = {}
    return create_async_engine(database_url, **kwargs)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def create_schema(engine: AsyncEngine) -> None:
    """Idempotent Base.metadata.create_all plus the C3 view v_alert_summary."""
    async with engine.begin() as conn:
        if engine.dialect.name == "sqlite":
            await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(Base.metadata.create_all)
        if engine.dialect.name == "sqlite":
            await conn.execute(text(f"CREATE VIEW IF NOT EXISTS v_alert_summary AS {V_ALERT_SUMMARY_SQL}"))
        else:
            await conn.execute(text(f"CREATE OR REPLACE VIEW v_alert_summary AS {V_ALERT_SUMMARY_SQL}"))
    log.info("Schema ready on %s", engine.dialect.name)


def make_mongo(mongo_url: str, database: str) -> Any:
    """Motor database; MONGO_URL=mock gives an in-process mongomock database."""
    if mongo_url == "mock":
        from mongomock_motor import AsyncMongoMockClient
        return AsyncMongoMockClient()[database]
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)
    return client[database]
