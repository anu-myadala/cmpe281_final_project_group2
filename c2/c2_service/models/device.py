"""The four tables C2 creates (Section 3.3.1 of the C2 design): device, device_command,
network_config and clip. Foreign keys point at vehicle (created by C1's schema in this repo);
the user columns hold app_user ids without a declared foreign key, as C3 does for its rows.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, LargeBinary, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, BigId, UtcDateTime, utcnow

DEVICE_TYPES = ("MIC_INTERIOR", "MIC_EXTERIOR", "CAMERA", "EDGE_GATEWAY")
MIC_TYPES = ("MIC_INTERIOR", "MIC_EXTERIOR")
DEVICE_STATUSES = ("PROVISIONING", "ONLINE", "OFFLINE", "FAULT", "DISABLED")
CONFIG_STATES = ("NONE", "PENDING", "CONFIRMED", "REJECTED", "TIMED_OUT")
COMMANDS = ("SET_CONFIG", "SET_NETWORK", "MUTE", "UNMUTE", "RESTART_AGENT", "FIRMWARE_UPDATE")
COMMAND_STATUSES = ("PENDING", "SENT", "APPLIED", "REJECTED", "FAILED", "TIMED_OUT", "SUPERSEDED")
CLIP_STATES = ("STORED", "DELETED", "EXPIRED")


class Device(Base):
    __tablename__ = "device"
    __table_args__ = (
        Index("ix_device_vehicle", "tenant_id", "vehicle_id"),
        Index("ix_device_sweep", "status", "last_seen_at"),
    )

    device_id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(BigId, nullable=False)
    vehicle_id: Mapped[int] = mapped_column(BigId, ForeignKey("vehicle.vehicle_id", name="fk_device_vehicle"),
                                            nullable=False)
    device_code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    device_type: Mapped[str] = mapped_column(String(16), nullable=False)
    position: Mapped[str] = mapped_column(String(32), nullable=False)
    serial_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    firmware_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PROVISIONING")
    muted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_seen_at: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)
    fault_reason: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    reported_ok: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    config_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    desired_config_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    config_state: Mapped[str] = mapped_column(String(16), nullable=False, default="NONE")
    created_at: Mapped[object] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)


class DeviceCommand(Base):
    __tablename__ = "device_command"
    __table_args__ = (
        Index("ix_cmd_device", "tenant_id", "device_id", "created_at"),
        Index("ix_cmd_timeout", "status", "sent_at"),
    )

    command_id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(BigId, nullable=False)
    vehicle_id: Mapped[int] = mapped_column(BigId, ForeignKey("vehicle.vehicle_id", name="fk_cmd_vehicle"),
                                            nullable=False)
    device_id: Mapped[Optional[int]] = mapped_column(BigId, ForeignKey("device.device_id", name="fk_cmd_device"),
                                                     nullable=True)
    issued_by: Mapped[Optional[int]] = mapped_column(BigId, nullable=True)
    command: Mapped[str] = mapped_column(String(20), nullable=False)
    params_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    sent_at: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)
    acked_at: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)
    result_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[object] = mapped_column(UtcDateTime, nullable=False, default=utcnow)


class NetworkConfig(Base):
    __tablename__ = "network_config"
    __table_args__ = (UniqueConstraint("vehicle_id", "priority", name="uq_network_priority"),)

    network_id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(BigId, nullable=False)
    vehicle_id: Mapped[int] = mapped_column(BigId, ForeignKey("vehicle.vehicle_id", name="fk_net_vehicle"),
                                            nullable=False)
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    ssid: Mapped[str] = mapped_column(String(32), nullable=False)
    security_type: Mapped[str] = mapped_column(String(12), nullable=False)
    password_enc: Mapped[Optional[bytes]] = mapped_column(LargeBinary(512), nullable=True)
    cellular_apn: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    updated_by: Mapped[Optional[int]] = mapped_column(BigId, nullable=True)
    updated_at: Mapped[object] = mapped_column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class Clip(Base):
    __tablename__ = "clip"
    __table_args__ = (
        UniqueConstraint("vehicle_id", "event_id", name="uq_clip_event"),
        Index("ix_clip_list", "tenant_id", "vehicle_id", "captured_at"),
        Index("ix_clip_expiry", "state", "expire_at"),
    )

    clip_id: Mapped[str] = mapped_column(String(8), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigId, nullable=False)
    vehicle_id: Mapped[int] = mapped_column(BigId, ForeignKey("vehicle.vehicle_id", name="fk_clip_vehicle"),
                                            nullable=False)
    device_id: Mapped[int] = mapped_column(BigId, ForeignKey("device.device_id", name="fk_clip_device"), nullable=False)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_rate: Mapped[int] = mapped_column(Integer, nullable=False)
    captured_at: Mapped[object] = mapped_column(UtcDateTime, nullable=False)
    uploaded_at: Mapped[object] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    expire_at: Mapped[object] = mapped_column(UtcDateTime, nullable=False)
    state: Mapped[str] = mapped_column(String(8), nullable=False, default="STORED")
    deleted_by: Mapped[Optional[int]] = mapped_column(BigId, nullable=True)
    deleted_at: Mapped[Optional[object]] = mapped_column(UtcDateTime, nullable=True)
