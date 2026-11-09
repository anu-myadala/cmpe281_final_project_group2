"""vehicle and vehicle_rider (Section 3.3.1 of the C2 design).

They are C2's tables, but in this repository C1's schema creates them (c1/api/app/models/c2c3.py)
and the seed fills them, so C2 maps the same columns and never issues DDL for them. Enums are
mapped as plain strings so a C1 change can't break a C2 read.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Integer, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, BigId, UtcDateTime, utcnow

VEHICLE_STATUSES = ("REGISTERED", "PROVISIONED", "ACTIVE", "RETIRED")


class Vehicle(Base):
    __tablename__ = "vehicle"

    vehicle_id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(BigId, nullable=False)
    vehicle_code: Mapped[str] = mapped_column(String(16), nullable=False)
    vin: Mapped[str] = mapped_column(String(17), nullable=False)
    make: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(40), nullable=False)
    model_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    retention_days: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=90)
    iot_thing_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    credential_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="REGISTERED")
    provisioned_at: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)
    retired_at: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[object] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[object] = mapped_column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class VehicleRider(Base):
    __tablename__ = "vehicle_rider"

    vehicle_id: Mapped[int] = mapped_column(BigId, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigId, primary_key=True)
    valid_from: Mapped[object] = mapped_column(UtcDateTime, primary_key=True, default=utcnow)
    valid_to: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)
    tenant_id: Mapped[int] = mapped_column(BigId, nullable=False)
