"""alert_rule: which sound class, in which vehicle state, raises which alert type,
from which confidence.

Design source: Deliverable #2, Part III, Section 3.3.1, Table 94 (row alert_rule,
with the generated column tenant_key = IFNULL(tenant_id, 0), because a unique key
ignores NULLs) and Table 95 (the seed of the platform defaults, tenant_id null). The
API over it is Section 2.2, Table 84, and the rules it serves are BR-C3-01 to BR-C3-05
and BR-C3-09/10 (Section 4). A tenant override is a row with tenant_id set that
replaces the default with the same class, state and type.

DEFAULT_RULES is stubs/common.py ALERT_RULES (rule ids 1 to 7), field for field, because
the seeded alert rows already reference these ids in alert.rule_id. The severity is
not stored: severity_override NULL means the alert type's default_severity (C1's
alert_type table).
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, Computed, Enum, ForeignKey, Numeric, SmallInteger, UniqueConstraint
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, BigId, UtcDateTime
from .shared import SEVERITIES

VEHICLE_STATES = ("ANY", "MOVING", "PARKED")
TinyInt = SmallInteger().with_variant(mysql.TINYINT(), "mysql", "mariadb")

# (rule_id, alert_type_code, class, vehicle_state, min_confidence, min_repeats, window_s, enabled)
DEFAULT_RULES = (
    (1, "EMERGENCY", "siren", "ANY", 0.60, 1, None, True),
    (2, "ACCIDENT", "crash", "MOVING", 0.70, 1, None, True),
    (3, "ACCIDENT", "glass_break", "MOVING", 0.70, 1, None, True),
    (4, "SECURITY", "glass_break", "PARKED", 0.70, 1, None, True),
    (5, "SECURITY", "scream", "PARKED", 0.60, 1, None, True),
    (6, "EMERGENCY", "scream", "MOVING", 0.60, 1, None, True),
    (7, "SAFETY", "honking", "ANY", 0.60, 2, 10, True),  # 2 events within 10 s
)


class AlertRule(Base):
    __tablename__ = "alert_rule"
    __table_args__ = (
        UniqueConstraint("tenant_key", "class_id", "vehicle_state", "alert_type_code", name="uq_rule_scope"),
    )

    rule_id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tenant.tenant_id", name="fk_rule_tenant"),
                                                     nullable=True)  # NULL = platform default
    tenant_key: Mapped[int] = mapped_column(BigId, Computed("IFNULL(tenant_id, 0)", persisted=True))
    alert_type_code: Mapped[str] = mapped_column(ForeignKey("alert_type.code", name="fk_rule_type"), nullable=False)
    class_id: Mapped[int] = mapped_column(ForeignKey("sound_class.class_id", name="fk_rule_class"), nullable=False)
    vehicle_state: Mapped[str] = mapped_column(Enum(*VEHICLE_STATES, name="rule_vehicle_state"), nullable=False)
    min_confidence: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False)
    severity_override: Mapped[Optional[str]] = mapped_column(Enum(*SEVERITIES, name="rule_severity"), nullable=True)
    min_repeats: Mapped[int] = mapped_column(TinyInt, nullable=False, default=1)
    window_s: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)  # only with min_repeats > 1
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[Optional[int]] = mapped_column(BigId, nullable=True)  # NULL for the seeded defaults
    updated_at: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)
