"""Who is calling and which tenant the request is about (Section 2.10 of the C2 design).

The user and service rules are the ones of stubs/common.py and c3_service/auth.py, so
swapping the stub for C2 changes nothing for C1 or the web app:

* a user JWT is HS256 signed by C1 with JWT_SECRET (claims sub, role, tenantId, name);
* a service token is HS256 signed with SERVICE_TOKEN_SECRET and has role SERVICE;
* tenant roles are scoped to their token's tenant; staff and service callers choose the
  tenant with tenantId (a number such as 2 or a code such as tB);
* a RIDER sees only the cars of the active trip.

C2 adds the device token (BR-C2-09): HS256 with the device secret, role DEVICE, claims
tenantId, vehicleId, ver; accepted only by POST /ingest/clips.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import jwt
from fastapi import Request
from sqlalchemy import select

from .config import Settings
from .errors import Forbidden, Unauthorized, Validation
from .models import STAFF_ROLES, Tenant, Vehicle, VehicleRider
from .timeutil import utcnow


@dataclass
class Caller:
    role: str
    user_id: Optional[int]
    name: str
    claim_tenant_id: Optional[int]
    tenant_id: Optional[int]
    tenant_code: Optional[str]
    token: str
    is_service: bool = False
    rider_vehicle_codes: Optional[List[str]] = None

    @property
    def is_staff(self) -> bool:
        return self.is_service or self.role in STAFF_ROLES


class TenantDirectory:
    """Tenant id <-> code, read from C1's tenant table and cached for the life of the process."""

    def __init__(self, session_factory: Any) -> None:
        self.session_factory = session_factory
        self._codes: Dict[int, str] = {}

    async def code_of(self, tenant_id: Optional[int]) -> Optional[str]:
        if tenant_id is None:
            return None
        if tenant_id not in self._codes:
            async with self.session_factory() as session:
                code = (await session.execute(select(Tenant.code).where(Tenant.tenant_id == tenant_id))).scalar_one_or_none()
            if code is None:
                return None
            self._codes[tenant_id] = code
        return self._codes[tenant_id]

    async def id_of(self, code: str) -> Optional[int]:
        for tenant_id, known in self._codes.items():
            if known == code:
                return tenant_id
        async with self.session_factory() as session:
            tenant_id = (await session.execute(select(Tenant.tenant_id).where(Tenant.code == code))).scalar_one_or_none()
        if tenant_id is not None:
            self._codes[tenant_id] = code
        return tenant_id


def bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise Unauthorized("Missing bearer token")
    return header[7:].strip()


def decode_token(token: str, settings: Settings) -> Dict[str, Any]:
    """The user secret first (refusing role SERVICE there), then the service secret (requiring role SERVICE)."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        if payload.get("role") == "SERVICE":
            raise Unauthorized("Invalid token")
        return payload
    except jwt.PyJWTError:
        pass
    try:
        payload = jwt.decode(token, settings.service_token_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise Unauthorized("Invalid token")
    if payload.get("role") != "SERVICE":
        raise Unauthorized("Invalid token")
    return payload


async def resolve_caller(request: Request, settings: Settings, session_factory: Any, tenants: TenantDirectory,
                         tenant_param: Optional[str], require_tenant: bool = True) -> Caller:
    token = bearer_token(request)
    payload = decode_token(token, settings)
    if payload.get("role") == "SERVICE":
        caller = Caller(role="SERVICE", user_id=None, name=str(payload.get("sub", "service")), claim_tenant_id=None,
                        tenant_id=None, tenant_code=None, token=token, is_service=True)
    else:
        try:
            user_id = int(payload["sub"])
        except (KeyError, ValueError, TypeError):
            raise Unauthorized("Invalid token")
        claim_tenant = payload.get("tenantId")
        caller = Caller(role=str(payload.get("role", "")), user_id=user_id, name=str(payload.get("name", "")),
                        claim_tenant_id=claim_tenant, tenant_id=claim_tenant, tenant_code=None, token=token)
    if caller.claim_tenant_id is None and tenant_param not in (None, ""):
        if str(tenant_param).lstrip("-").isdigit():
            caller.tenant_id = int(tenant_param)
        else:
            caller.tenant_id = await tenants.id_of(str(tenant_param))
            if caller.tenant_id is None:
                raise Validation("Unknown tenant", {"field": "tenantId"})
    if caller.tenant_id is None and require_tenant:
        if caller.is_service:
            raise Validation("tenantId is required", {"field": "tenantId"})
        raise Validation("tenantId is required for staff roles", {"field": "tenantId"})
    if caller.tenant_id is not None:
        caller.tenant_code = await tenants.code_of(caller.tenant_id)
        if caller.tenant_code is None:
            raise Validation("Unknown tenant", {"field": "tenantId"})
    if caller.role == "RIDER":
        caller.rider_vehicle_codes = await rider_vehicles(session_factory, caller.user_id)
    return caller


async def rider_vehicles(session_factory: Any, user_id: Optional[int], now: Optional[datetime] = None) -> List[str]:
    now = now or utcnow()
    async with session_factory() as session:
        rows = (await session.execute(
            select(Vehicle.vehicle_code).join(VehicleRider, VehicleRider.vehicle_id == Vehicle.vehicle_id)
            .where(VehicleRider.user_id == user_id, VehicleRider.valid_from <= now,
                   (VehicleRider.valid_to.is_(None)) | (VehicleRider.valid_to > now)))).scalars().all()
    return list(rows)


def forbid_roles(caller: Caller, *roles: str) -> None:
    if caller.role in roles:
        raise Forbidden()


def require_roles(caller: Caller, *roles: str) -> None:
    if caller.role not in roles:
        raise Forbidden()


# -- device tokens (BR-C2-09) ---------------------------------------------------------------------------
def issue_device_token(settings: Settings, tenant_code: str, tenant_id: int, vehicle_code: str, version: int) -> str:
    now = int(time.time())
    return jwt.encode({"sub": f"{tenant_code}-{vehicle_code}", "role": "DEVICE", "tenantId": tenant_code,
                       "tenantNo": tenant_id, "vehicleId": vehicle_code, "ver": version, "iat": now,
                       "exp": now + settings.device_token_days * 86400, "jti": uuid.uuid4().hex},
                      settings.device_secret, algorithm="HS256")


def decode_device_token(token: str, settings: Settings) -> Dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.device_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise Unauthorized("Invalid device token")
    if payload.get("role") != "DEVICE" or not payload.get("vehicleId"):
        raise Unauthorized("Invalid device token")
    return payload
