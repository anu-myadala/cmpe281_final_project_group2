"""Mappings of the C1 and C2 tables that C3 reads (Part III, Section 1.5, Table 82, and
Section 2.6, Table 88: "SELECT-only through C5"), and C1's audit_log, the one C1 table C3
writes: C1's AuditLogger is "shared with C2 and C3, which pass their own action names",
and Table 84 audits RULE_SAVE and RULE_DELETE (team decision, step 9).

C3 never writes or creates these tables, so only the columns C3 needs are mapped.
Enum columns are mapped as plain strings, so a value that C1 adds later can't break a
C3 read. The names and keys are those of c1/api/app/models (c1.py and c2c3.py).
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import JSON, Boolean, Integer, SmallInteger, String, Time
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, BigId, UtcDateTime, utcnow

# Values shared with C1 (c1/api/app/models): roles, alert statuses, alert types.
TENANT_ROLES = ("OWNER", "FLEET_MANAGER", "RIDER")
STAFF_ROLES = ("IOT_ENGINEER", "SERVICE_STAFF", "SYS_ADMIN")
ALERT_STATUSES = ("NEW", "ACKNOWLEDGED", "RESOLVED", "FALSE_ALARM")
SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


class Tenant(Base):
    """C1's tenant: the code (tA) is the MongoDB tenantId; time_zone is for quiet hours (Table 104)."""
    __tablename__ = "tenant"

    tenant_id: Mapped[int] = mapped_column(BigId, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    time_zone: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class AppUser(Base):
    """C1's users: display names for acknowledgedBy, and the email recipients."""
    __tablename__ = "app_user"

    user_id: Mapped[int] = mapped_column(BigId, primary_key=True)
    tenant_id: Mapped[Optional[int]] = mapped_column(BigId, nullable=True)
    email: Mapped[str] = mapped_column(String(190), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class AlertType(Base):
    """C1's alert types: the default severity of a rule without severity_override (Table 103)."""
    __tablename__ = "alert_type"

    code: Mapped[str] = mapped_column(String(24), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    default_severity: Mapped[str] = mapped_column(String(16), nullable=False)
    escalation_minutes: Mapped[int] = mapped_column(Integer, nullable=False)


class Vehicle(Base):
    """C2's vehicle: vehicle_id is the BIGINT key, vehicle_code the public id (AV-01) used in alerts."""
    __tablename__ = "vehicle"

    vehicle_id: Mapped[int] = mapped_column(BigId, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigId, nullable=False)
    vehicle_code: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    retention_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class VehicleRider(Base):
    """C2's vehicle_rider: a RIDER sees the cars whose row covers now (BR-03 of C1)."""
    __tablename__ = "vehicle_rider"

    vehicle_id: Mapped[int] = mapped_column(BigId, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigId, primary_key=True)
    valid_from: Mapped[object] = mapped_column(UtcDateTime, primary_key=True)
    valid_to: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)
    tenant_id: Mapped[int] = mapped_column(BigId, nullable=False)


class Subscription(Base):
    """C1's subscription: service switch, channels (CSV), quiet hours, notify_riders (BR-05 of C1)."""
    __tablename__ = "subscription"

    subscription_id: Mapped[int] = mapped_column(BigId, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigId, nullable=False)
    vehicle_id: Mapped[int] = mapped_column(BigId, nullable=False)  # vehicle.vehicle_id, not the code
    service_code: Mapped[str] = mapped_column(String(24), nullable=False)  # = alert_type_code
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    channels: Mapped[str] = mapped_column(String(40), nullable=False)
    quiet_from: Mapped[Optional[object]] = mapped_column(Time, nullable=True)
    quiet_to: Mapped[Optional[object]] = mapped_column(Time, nullable=True)
    notify_riders: Mapped[bool] = mapped_column(Boolean, nullable=False)


class AuditLog(Base):
    """C1's audit_log, all columns as c1/api/app/models/c1.py defines them (BR-12 of C1). C3 inserts the same
    row C1's AuditLogger.write builds; it never creates or alters the table."""
    __tablename__ = "audit_log"

    audit_id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[Optional[int]] = mapped_column(BigId, nullable=True)
    tenant_id: Mapped[Optional[int]] = mapped_column(BigId, nullable=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    entity: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    before_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    after_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    ts: Mapped[object] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
