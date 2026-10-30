"""FastAPI dependencies shared by the routers: the container, one database
session per request, the verified claims of the bearer token, the service
token check for /internal/*, and the request scope (BR-03)."""
from __future__ import annotations

from typing import AsyncIterator, Optional

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .container import AppContainer
from .core.rbac import Scope, scope_dependency
from .core.security import Claims
from .errors import Unauthorized


def get_container(request: Request) -> AppContainer:
    return request.app.state.container


async def get_session(container: AppContainer = Depends(get_container)) -> AsyncIterator[AsyncSession]:
    async with container.session_factory() as session:
        yield session


def _bearer(request: Request) -> Optional[str]:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


async def current_claims(request: Request, container: AppContainer = Depends(get_container)) -> Claims:
    token = _bearer(request)
    if not token:
        raise Unauthorized("Missing bearer token")
    return container.tokens.verify(token)


async def require_service_token(request: Request, container: AppContainer = Depends(get_container)) -> dict:
    token = _bearer(request)
    if not token:
        raise Unauthorized("Missing service token")
    return container.tokens.verify_service_token(token)


async def current_scope(request: Request, claims: Claims = Depends(current_claims),
                        session: AsyncSession = Depends(get_session)) -> Scope:
    return await scope_dependency(request, claims, session)


def client_ip(request: Request) -> Optional[str]:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
