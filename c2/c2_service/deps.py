"""FastAPI dependencies: the handles opened at startup (app.state) and the callers.

    any_caller      every C2 read: users of every role (scoped) and the service token
    service_caller  the internal routes: a service token only (401 without, 403 for a user token)
The write guards (IOT_ENGINEER only, owner and fleet manager on their own cars) are applied
in the routes with require_roles, following Table 15 of the C2 design.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import Depends, Query, Request

from .auth import Caller, TenantDirectory, bearer_token, decode_token, resolve_caller
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


def get_services(request: Request) -> Any:
    """The service objects built once in the lifespan (main.Services)."""
    return request.app.state.services


async def any_caller(request: Request, tenantId: Optional[str] = Query(default=None),
                     settings: Settings = Depends(get_settings_dep),
                     session_factory: Any = Depends(get_session_factory),
                     tenants: TenantDirectory = Depends(get_tenants)) -> Caller:
    return await resolve_caller(request, settings, session_factory, tenants, tenantId, require_tenant=False)


async def service_caller(request: Request, settings: Settings = Depends(get_settings_dep)) -> dict:
    payload = decode_token(bearer_token(request), settings)
    if payload.get("role") != "SERVICE":
        raise Forbidden("A service token is required")
    return payload
