"""FastAPI dependencies: the handles opened at startup (app.state) and the role guards
each router declares (Part III, Section 2.7, Table 89).

    alert_caller  GET /alerts, /alerts/map, /alerts/{id}, POST .../actions: IOT_ENGINEER 403
    rules_caller  /alert-rules: also OWNER and RIDER 403

Both leave the tenant optional (require_tenant=False, as in the stub): each route
decides whether it needs one, for example GET /alerts answers 400 without a tenant.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import Depends, Query, Request

from .auth import Caller, TenantDirectory, bearer_token, decode_token, forbid_roles, resolve_caller
from .config import Settings
from .errors import Forbidden


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_session_factory(request: Request) -> Any:
    return request.app.state.session_factory


def get_mongo(request: Request) -> Any:
    return request.app.state.mongo


def get_tenants(request: Request) -> TenantDirectory:
    return request.app.state.tenants


async def alert_caller(request: Request, tenantId: Optional[str] = Query(default=None),
                       settings: Settings = Depends(get_settings_dep),
                       session_factory: Any = Depends(get_session_factory),
                       tenants: TenantDirectory = Depends(get_tenants)) -> Caller:
    caller = await resolve_caller(request, settings, session_factory, tenants, tenantId, require_tenant=False)
    forbid_roles(caller, "IOT_ENGINEER")  # BR-01 of C1: no alert pages for the IoT engineer
    return caller


async def rules_caller(caller: Caller = Depends(alert_caller)) -> Caller:
    forbid_roles(caller, "OWNER", "RIDER")  # Table 89: rules are for fleet managers and staff
    return caller


def get_publisher(request: Request) -> Any:
    return request.app.state.publisher


def get_c1_client(request: Request) -> Any:
    return request.app.state.c1_client


async def service_caller(request: Request, settings: Settings = Depends(get_settings_dep)) -> dict:
    """The internal routes (Table 89): a service token only. No or a bad token is 401; a valid user token 403."""
    payload = decode_token(bearer_token(request), settings)
    if payload.get("role") != "SERVICE":
        raise Forbidden("A service token is required")
    return payload
