"""C1's tables that C2 reads (tenant, app_user), and C1's audit_log, to which C2 adds rows the way
C1's AuditLogger writes them (provisioning, retire, clip delete). C2 never creates or alters
these tables; only the columns C2 needs are mapped, enums as plain strings.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, BigId, UtcDateTime, utcnow

TENANT_ROLES = ("OWNER", "FLEET_MANAGER", "RIDER")
STAFF_ROLES = ("IOT_ENGINEER", "SERVICE_STAFF", "SYS_ADMIN")


class Tenant(Base):
    __tablename__ = "tenant"

    tenant_id: Mapped[int] = mapped_column(BigId, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    plan: Mapped[str] = mapped_column(String(16), nullable=False)
    max_vehicles: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class AppUser(Base):
    __tablename__ = "app_user"

    user_id: Mapped[int] = mapped_column(BigId, primary_key=True)
    tenant_id: Mapped[Optional[int]] = mapped_column(BigId, nullable=True)
    email: Mapped[str] = mapped_column(String(190), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class AuditLog(Base):
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
