"""SQLAlchemy models of C2.

shared.py   C1's tables that C2 reads (tenant, app_user) and audit_log, which C2 writes rows to
vehicle.py  vehicle and vehicle_rider: C2's tables by the design, created by C1's schema in this repo
device.py   the tables C2 creates itself: device, device_command, network_config, clip
"""
from .base import Base, BigId, UtcDateTime, utcnow
from .device import (CLIP_STATES, COMMAND_STATUSES, COMMANDS, CONFIG_STATES, DEVICE_STATUSES, DEVICE_TYPES, MIC_TYPES,
                     Clip, Device, DeviceCommand, NetworkConfig)
from .shared import STAFF_ROLES, TENANT_ROLES, AppUser, AuditLog, Tenant
from .vehicle import VEHICLE_STATUSES, Vehicle, VehicleRider

__all__ = [
    "Base", "BigId", "UtcDateTime", "utcnow",
    "Clip", "Device", "DeviceCommand", "NetworkConfig",
    "CLIP_STATES", "COMMAND_STATUSES", "COMMANDS", "CONFIG_STATES", "DEVICE_STATUSES", "DEVICE_TYPES", "MIC_TYPES",
    "AppUser", "AuditLog", "Tenant", "STAFF_ROLES", "TENANT_ROLES",
    "Vehicle", "VehicleRider", "VEHICLE_STATUSES",
]
