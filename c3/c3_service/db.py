"""Database access: the async SQLAlchemy engine and sessions (MySQL, or SQLite for
tests and developer mode) and the Motor client for MongoDB (or mongomock with
MONGO_URL=mock). One session per request (see deps.py from step 3 on)."""
from __future__ import annotations

from typing import Any

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
