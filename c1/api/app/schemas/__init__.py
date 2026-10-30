"""Pydantic request bodies (camelCase on the wire). A body that does not
match one of these answers VALIDATION / 400."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Role = Literal["OWNER", "FLEET_MANAGER", "RIDER", "IOT_ENGINEER", "SERVICE_STAFF", "SYS_ADMIN"]
Plan = Literal["BASIC", "FLEET", "ENTERPRISE"]
TenantType = Literal["OWNER", "FLEET", "SERVICE_PROVIDER"]
Channel = Literal["DASHBOARD", "EMAIL", "SMS", "PUSH"]
Severity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
FeedbackKind = Literal["CONFIRMED", "FALSE_ALARM", "WRONG_CLASS"]

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
TIME_RE = re.compile(r"^\d{2}:\d{2}(:\d{2})?$")


class Body(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# -- auth ------------------------------------------------------------------------
class LoginBody(Body):
    email: str = Field(min_length=3, max_length=190)
    password: str = Field(min_length=1, max_length=200)


class PasswordChangeBody(Body):
    currentPassword: str = Field(min_length=1, max_length=200)
    newPassword: str = Field(min_length=1, max_length=200)


# -- subscriptions ---------------------------------------------------------------------
class ServiceSubscriptionBody(Body):
    serviceCode: str = Field(min_length=1, max_length=24)
    enabled: bool = True
    channels: List[Channel] = Field(default_factory=lambda: ["DASHBOARD"])
    quietFrom: Optional[str] = None
    quietTo: Optional[str] = None

    @field_validator("quietFrom", "quietTo")
    @classmethod
    def _time(cls, value):
        if value in (None, ""):
            return None
        if not TIME_RE.match(value):
            raise ValueError("must be HH:MM")
        return value[:5]


class SubscriptionBody(Body):
    services: List[ServiceSubscriptionBody] = Field(default_factory=list)
    notifyRiders: bool = False


# -- admin ----------------------------------------------------------------------------
class TenantCreateBody(Body):
    name: str = Field(min_length=2, max_length=120)
    tenantType: TenantType
    plan: Plan
    timeZone: Optional[str] = Field(default=None, max_length=40)


class TenantPatchBody(Body):
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    plan: Optional[Plan] = None
    status: Optional[Literal["ACTIVE", "SUSPENDED"]] = None
    timeZone: Optional[str] = Field(default=None, max_length=40)


class UserCreateBody(Body):
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    role: Role
    tenantId: Optional[int] = None


class UserPatchBody(Body):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    role: Optional[Role] = None
    status: Optional[Literal["ACTIVE", "DISABLED"]] = None
    tenantId: Optional[int] = None
    # tenantId=null in the body means "clear the tenant"; absent means "keep".
    model_config = ConfigDict(extra="ignore")


class AlertTypeBody(Body):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    defaultSeverity: Optional[Severity] = None
    escalationMinutes: Optional[int] = Field(default=None, ge=0, le=1440)
    description: Optional[str] = Field(default=None, max_length=255)


# -- model registry ---------------------------------------------------------------------
class ModelCreateBody(Body):
    name: str = Field(min_length=1, max_length=80)
    task: str = Field(min_length=1, max_length=120)


class VersionCreateBody(Body):
    version: str = Field(min_length=5, max_length=32)
    notes: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("version")
    @classmethod
    def _semver(cls, value: str) -> str:
        if not SEMVER.match(value):
            raise ValueError("version must be semver (MAJOR.MINOR.PATCH)")
        return value


class MetricsBody(Body):
    macroF1: float = Field(ge=0, le=1)
    accuracy: float = Field(ge=0, le=1)
    perClass: Dict[str, Any] = Field(default_factory=dict)


class VersionCompleteBody(Body):
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    sizeBytes: int = Field(ge=1)
    metrics: MetricsBody


class ActivateBody(Body):
    force: bool = False
    note: Optional[str] = Field(default=None, max_length=1000)


class RollbackBody(Body):
    note: Optional[str] = Field(default=None, max_length=1000)


class FeedbackBody(Body):
    label: FeedbackKind
    correctedClass: Optional[str] = Field(default=None, max_length=32)


# -- internal ------------------------------------------------------------------------
class InternalEventBody(Body):
    topic: Literal["alert.created", "alert.updated", "device.status", "model.activated"]
    payload: Dict[str, Any] = Field(default_factory=dict)
    tenantId: Optional[Any] = None


class SubscriptionDefaultsBody(Body):
    vehicleId: str = Field(min_length=1, max_length=16)
    tenantId: int
