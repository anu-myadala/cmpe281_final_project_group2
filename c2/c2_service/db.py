"""Database access: the async SQLAlchemy engine and sessions (MySQL, or SQLite for
tests and developer mode) and the Motor client for MongoDB (or mongomock with
MONGO_URL=mock). One session per request (see deps.py from step 3 on)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def make_engine(database_url: str) -> AsyncEngine:
    kwargs: dict = {} if database_url.startswith("sqlite") else {"pool_pre_ping": True}
    return create_async_engine(database_url, **kwargs)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


def make_mongo(mongo_url: str, database: str) -> Any:
    """The Motor database handle; MONGO_URL=mock gives an in-process mongomock database."""
    if mongo_url == "mock":
        from mongomock_motor import AsyncMongoMockClient
        return AsyncMongoMockClient()[database]
    from motor.motor_asyncio import AsyncIOMotorClient
    return AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)[database]


@asynccontextmanager
async def named_lock(engine: AsyncEngine, name: str, wait_s: int = 0) -> AsyncIterator[bool]:
    """MySQL GET_LOCK around a block, so that only one C2 replica runs it at a time (startup schema,
    the sweep, the command timeouts, the retention job). Yields whether the lock was taken; the lock
    belongs to the connection, so it is released if the replica dies. SQLite (tests): always True."""
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

