"""Read-only models of the C2 and C3 tables that C1 reads (Parts II and III).

C1 never writes these rows (the seed script fills them for the demo). The
columns follow the DDL of Part II (vehicle, vehicle_rider) and Part III
(alert, alert_action); the C3 view v_alert_summary is created next to them
by app.db.create_schema.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import (Boolean, Enum, Float, ForeignKey, Index, Integer, Numeric, SmallInteger, String,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, BigId, UtcDateTime, utcnow

VEHICLE_STATUSES = ("REGISTERED", "PROVISIONED", "ACTIVE", "RETIRED")
ALERT_STATUSES = ("NEW", "ACKNOWLEDGED", "RESOLVED", "FALSE_ALARM")
ALERT_TYPES = ("EMERGENCY", "ACCIDENT", "SECURITY", "SAFETY", "PASSENGER", "VEHICLE_HEALTH")
OPEN_STATUSES = ("NEW", "ACKNOWLEDGED")

# Seven sound classes in the seed order of Part III (Table 95); class_id = position + 1.
SOUND_CLASSES = ("siren", "crash", "glass_break", "scream", "honking", "traffic", "environmental")


def sound_class_id(name: str) -> int:
    return SOUND_CLASSES.index(name) + 1


def sound_class_name(class_id: Optional[int]) -> Optional[str]:
    if class_id is None or not 1 <= class_id <= len(SOUND_CLASSES):
        return None
    return SOUND_CLASSES[class_id - 1]


class Vehicle(Base):
    __tablename__ = "vehicle"
    __table_args__ = (
        UniqueConstraint("tenant_id", "vehicle_code", name="uq_vehicle_code"),
        Index("ix_vehicle_status", "tenant_id", "status"),
    )

    vehicle_id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.tenant_id", name="fk_vehicle_tenant"), nullable=False)
    vehicle_code: Mapped[str] = mapped_column(String(16), nullable=False)
    vin: Mapped[str] = mapped_column(String(17), nullable=False, unique=True)
    make: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(40), nullable=False)
    model_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    retention_days: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=90)
    iot_thing_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    credential_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(Enum(*VEHICLE_STATUSES, name="vehicle_status"), nullable=False,
                                        default="REGISTERED")
    provisioned_at: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)
    retired_at: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[object] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[object] = mapped_column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class VehicleRider(Base):
    __tablename__ = "vehicle_rider"
    __table_args__ = (Index("ix_rider_tenant_vehicle", "tenant_id", "vehicle_id", "valid_to"),)

    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.vehicle_id", name="fk_rider_vehicle"),
                                            primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.user_id", name="fk_rider_user"), primary_key=True)
    valid_from: Mapped[object] = mapped_column(UtcDateTime, primary_key=True, default=utcnow)
    valid_to: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)
    tenant_id: Mapped[int] = mapped_column(BigId, nullable=False)


class Alert(Base):
    __tablename__ = "alert"
    __table_args__ = (
        UniqueConstraint("event_ref", "alert_type_code", name="uq_alert_event_type"),
        Index("ix_alert_queue", "tenant_id", "status", "created_at"),
        Index("ix_alert_vehicle", "tenant_id", "vehicle_id", "created_at"),
    )

    alert_id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.tenant_id", name="fk_alert_tenant"), nullable=False)
    vehicle_id: Mapped[str] = mapped_column(String(32), nullable=False)  # public code, e.g. AV-01
    alert_type_code: Mapped[str] = mapped_column(ForeignKey("alert_type.code", name="fk_alert_type"),
                                                 nullable=False)
    rule_id: Mapped[int] = mapped_column(BigId, nullable=False)
    top_class_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    severity: Mapped[str] = mapped_column(Enum("LOW", "MEDIUM", "HIGH", "CRITICAL", name="alert_severity_c3"),
                                          nullable=False)
    status: Mapped[str] = mapped_column(Enum(*ALERT_STATUSES, name="alert_status"), nullable=False, default="NEW")
    event_ref: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    vehicle_state: Mapped[str] = mapped_column(Enum("MOVING", "PARKED", name="vehicle_state"), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    count: Mapped[int] = mapped_column("count", Integer, nullable=False, default=1)
    lat: Mapped[Optional[float]] = mapped_column(Float(53), nullable=True)
    lon: Mapped[Optional[float]] = mapped_column(Float(53), nullable=True)
    created_at: Mapped[object] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[object] = mapped_column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)
    acknowledged_by: Mapped[Optional[int]] = mapped_column(BigId, nullable=True)
    acknowledged_at: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)
    closed_by: Mapped[Optional[int]] = mapped_column(BigId, nullable=True)
    closed_at: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)


class AlertAction(Base):
    __tablename__ = "alert_action"
    __table_args__ = (Index("ix_action_alert_ts", "alert_id", "ts"),)

    action_id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alert.alert_id", name="fk_action_alert"), nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(BigId, nullable=True)
    action: Mapped[str] = mapped_column(Enum("CREATE", "ACKNOWLEDGE", "RESOLVE", "FALSE_ALARM", name="alert_action_kind"),
                                        nullable=False)
    from_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ts: Mapped[object] = mapped_column(UtcDateTime, nullable=False, default=utcnow)


# The C3 view read by C1 for statistics (Part III, Section 3.3).
V_ALERT_SUMMARY_SQL = (
    "SELECT alert_id, tenant_id, vehicle_id, alert_type_code, severity, status, top_class_id, "
    "confidence, created_at, acknowledged_at, closed_at FROM alert"
)
