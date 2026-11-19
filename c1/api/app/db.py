"""DataAccess (C5): the SQLAlchemy async engine, the session factory, the
schema creation at startup and the MongoDB (Motor) client."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

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


@asynccontextmanager
async def named_lock(engine: AsyncEngine, name: str, wait_s: int = 0) -> AsyncIterator[bool]:
    """MySQL GET_LOCK around a block, so that API replicas that start together on an empty database
    create the tables one at a time (two concurrent CREATE TABLE runs fail with MySQL error 1684).
    Yields whether the lock was taken. SQLite (tests): always True."""
    if engine.dialect.name != "mysql":
        yield True
        return
    async with engine.connect() as conn:
        got = bool((await conn.execute(text("SELECT GET_LOCK(:n, :w)"), {"n": name, "w": wait_s})).scalar())
        try:
            yield got
        finally:
            if got:
                await conn.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": name})


async def create_schema(engine: AsyncEngine) -> None:
    """Idempotent Base.metadata.create_all plus the C3 view v_alert_summary, one replica at a time."""
    async with named_lock(engine, "scicp_c1_schema", wait_s=60):
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
