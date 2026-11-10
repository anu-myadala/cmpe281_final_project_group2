"""Declarative base of C2 and the column types shared by every table.

C2 has its own Base (its own metadata), separate from C1's, so that creating the
C2 schema can never touch a table C1 creates. BigId and UtcDateTime are the same
definitions as C1's app/models/base.py, so the mirrored tables compile to the
same DDL: BIGINT and DATETIME(3) on MySQL, INTEGER and DATETIME on SQLite.
"""
from __future__ import annotations

from sqlalchemy import BigInteger, DateTime, Integer
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import DeclarativeBase

from ..timeutil import utcnow

__all__ = ["Base", "BigId", "UtcDateTime", "utcnow"]


class Base(DeclarativeBase):
    pass


# BIGINT on MySQL; SQLite only auto-increments INTEGER primary keys.
BigId = BigInteger().with_variant(Integer, "sqlite")

# DATETIME(3) on MySQL and MariaDB, plain DATETIME elsewhere. Values are naive UTC.
UtcDateTime = DateTime().with_variant(mysql.DATETIME(fsp=3), "mysql", "mariadb")
