"""MySQL tables owned by C1 and the C4 registry (Part I, Table 21)."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import (JSON, Boolean, Enum, ForeignKey, Index, Integer, String, Text, Time,
                        UniqueConstraint, text)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, BigId, UtcDateTime, utcnow

ROLES = ("OWNER", "FLEET_MANAGER", "RIDER", "IOT_ENGINEER", "SERVICE_STAFF", "SYS_ADMIN")
TENANT_ROLES = ("OWNER", "FLEET_MANAGER", "RIDER")
STAFF_ROLES = ("IOT_ENGINEER", "SERVICE_STAFF", "SYS_ADMIN")
PLANS = ("BASIC", "FLEET", "ENTERPRISE")
PLAN_MAX_VEHICLES = {"BASIC": 3, "FLEET": 50, "ENTERPRISE": 500}
CHANNELS = ("DASHBOARD", "EMAIL", "SMS", "PUSH")
SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
VERSION_STATUSES = ("UPLOADING", "REGISTERED", "ACTIVE", "INACTIVE", "FAILED")
FEEDBACK_LABELS = ("CONFIRMED", "FALSE_ALARM", "WRONG_CLASS")


class Tenant(Base):
    __tablename__ = "tenant"

    tenant_id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    tenant_type: Mapped[str] = mapped_column(Enum("OWNER", "FLEET", "SERVICE_PROVIDER", name="tenant_type"),
                                             nullable=False)
    plan: Mapped[str] = mapped_column(Enum(*PLANS, name="tenant_plan"), nullable=False)
    max_vehicles: Mapped[int] = mapped_column(Integer, nullable=False)
    time_zone: Mapped[str] = mapped_column(String(40), nullable=False, default="America/Los_Angeles",
                                           server_default="America/Los_Angeles")
    status: Mapped[str] = mapped_column(Enum("ACTIVE", "SUSPENDED", name="tenant_status"), nullable=False,
                                        default="ACTIVE")
    created_at: Mapped[object] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[object] = mapped_column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class AppUser(Base):
    __tablename__ = "app_user"
    __table_args__ = (Index("ix_user_tenant_role", "tenant_id", "role"),)

    user_id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tenant.tenant_id", name="fk_user_tenant"),
                                                     nullable=True)
    email: Mapped[str] = mapped_column(String(190), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(Enum(*ROLES, name="user_role"), nullable=False)
    status: Mapped[str] = mapped_column(Enum("ACTIVE", "DISABLED", "LOCKED", name="user_status"),
                                        nullable=False, default="ACTIVE")
    failed_logins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_failed_at: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)
    locked_until: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[object] = mapped_column(UtcDateTime, nullable=False, default=utcnow)


class RefreshToken(Base):
    __tablename__ = "refresh_token"

    token_id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.user_id", name="fk_rt_user"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[object] = mapped_column(UtcDateTime, nullable=False)
    revoked_at: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)


class IntelligenceService(Base):
    __tablename__ = "intelligence_service"

    service_code: Mapped[str] = mapped_column(String(24), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Subscription(Base):
    __tablename__ = "subscription"
    __table_args__ = (
        UniqueConstraint("vehicle_id", "service_code", name="uq_sub_vehicle_service"),
        Index("ix_sub_tenant_vehicle", "tenant_id", "vehicle_id"),
    )

    subscription_id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.tenant_id", name="fk_sub_tenant"), nullable=False)
    # BIGINT key of the C2 vehicle table; the API uses the public code (AV-01).
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.vehicle_id", name="fk_sub_vehicle"), nullable=False)
    service_code: Mapped[str] = mapped_column(
        ForeignKey("intelligence_service.service_code", name="fk_sub_service"), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Comma-separated subset of DASHBOARD,EMAIL,SMS,PUSH (VARCHAR(40) everywhere, see the brief).
    channels: Mapped[str] = mapped_column(String(40), nullable=False, default="DASHBOARD")
    quiet_from: Mapped[Optional[object]] = mapped_column(Time, nullable=True)
    quiet_to: Mapped[Optional[object]] = mapped_column(Time, nullable=True)
    notify_riders: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by: Mapped[Optional[int]] = mapped_column(ForeignKey("app_user.user_id", name="fk_sub_user"),
                                                      nullable=True)
    updated_at: Mapped[object] = mapped_column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    @property
    def channel_list(self) -> list:
        return [c for c in self.channels.split(",") if c]


class AlertType(Base):
    __tablename__ = "alert_type"

    code: Mapped[str] = mapped_column(String(24), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    default_severity: Mapped[str] = mapped_column(Enum(*SEVERITIES, name="alert_severity"), nullable=False)
    escalation_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_tenant_ts", "tenant_id", "ts"), Index("ix_audit_entity", "entity", "entity_id"))

    audit_id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("app_user.user_id", name="fk_audit_user"),
                                                         nullable=True)
    tenant_id: Mapped[Optional[int]] = mapped_column(BigId, nullable=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    entity: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    before_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    after_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    ts: Mapped[object] = mapped_column(UtcDateTime, nullable=False, default=utcnow)


class AiModel(Base):
    __tablename__ = "ai_model"

    model_id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    task: Mapped[str] = mapped_column(String(120), nullable=False)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("app_user.user_id", name="fk_model_user"),
                                                      nullable=True)
    created_at: Mapped[object] = mapped_column(UtcDateTime, nullable=False, default=utcnow)


class ModelVersion(Base):
    __tablename__ = "model_version"
    __table_args__ = (
        UniqueConstraint("model_id", "version", name="uq_mv_model_version"),
        Index("ix_mv_model_status", "model_id", "status"),
    )

    version_id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("ai_model.model_id", name="fk_mv_model"), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(Enum(*VERSION_STATUSES, name="version_status"), nullable=False,
                                        default="UPLOADING", server_default=text("'UPLOADING'"))
    artifact_uri: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(BigId, nullable=True)
    metrics_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("app_user.user_id", name="fk_mv_user"),
                                                      nullable=True)
    created_at: Mapped[object] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    activated_at: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)
    deactivated_at: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)

    @property
    def artifact_key(self) -> str:
        """Object key inside the models bucket (models/{modelId}/{version}/model.pt)."""
        uri = self.artifact_uri
        if uri.startswith("s3://"):
            return uri[5:].split("/", 1)[1]
        return uri

    @property
    def macro_f1(self) -> Optional[float]:
        if not self.metrics_json:
            return None
        value = self.metrics_json.get("macroF1")
        return float(value) if value is not None else None


class FeedbackLabel(Base):
    __tablename__ = "feedback_label"
    __table_args__ = (UniqueConstraint("event_ref", "user_id", name="uq_feedback_event_user"),)

    label_id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    event_ref: Mapped[str] = mapped_column(String(24), nullable=False)
    version_id: Mapped[int] = mapped_column(ForeignKey("model_version.version_id", name="fk_fb_version"),
                                            nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.user_id", name="fk_fb_user"), nullable=False)
    label: Mapped[str] = mapped_column(Enum(*FEEDBACK_LABELS, name="feedback_label_kind"), nullable=False)
    corrected_class: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    ts: Mapped[object] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
