"""Declarative base and the column types shared by every table.

The DDL of Part I Section 3.3 is written for MySQL 8 (BIGINT AUTO_INCREMENT
keys, DATETIME(3) in UTC, ENUM columns, JSON). The variants below keep that
DDL on MySQL/MariaDB while letting the same models run on SQLite for the
tests and the developer mode.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# BIGINT on MySQL; SQLite only auto-increments INTEGER primary keys.
BigId = BigInteger().with_variant(Integer, "sqlite")

# DATETIME(3) on MySQL and MariaDB, plain DATETIME elsewhere. Values are naive UTC.
UtcDateTime = DateTime().with_variant(mysql.DATETIME(fsp=3), "mysql", "mariadb")


def utcnow() -> datetime:
    """Current time as a naive UTC datetime (what the DATETIME columns store)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
