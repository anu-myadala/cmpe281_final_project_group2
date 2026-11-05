"""alert and alert_action (Part III, Section 3.3.1, Table 94).

By the team's decision C1 creates these two tables (app.db.create_schema, with the
view v_alert_summary) and the seed fills them; C3 reads and writes the rows but never
creates or alters the tables. The two class bodies below are copied character for
character from c1/api/app/models/c2c3.py, and tests/test_models.py checks that
they still match, so a change on either side fails the tests.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Enum, Float, ForeignKey, Index, Integer, Numeric, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, BigId, UtcDateTime, utcnow
from .shared import ALERT_STATUSES

# Alert lifecycle (Table 105): OPEN = NEW or ACKNOWLEDGED, CLOSED = RESOLVED or FALSE_ALARM.
OPEN_STATUSES = ("NEW", "ACKNOWLEDGED")
CLOSED_STATUSES = ("RESOLVED", "FALSE_ALARM")


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
