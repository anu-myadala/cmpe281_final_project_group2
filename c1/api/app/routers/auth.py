"""AuthRouter: /auth/login, /auth/refresh, /auth/logout, /auth/me, /auth/password/change."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..container import AppContainer
from ..core.security import Claims
from ..deps import client_ip, current_claims, get_container, get_session
from ..schemas import LoginBody, PasswordChangeBody
from ..services import AuthService
from ..services.auth_service import user_dto

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refreshToken"


def _cookie_path(container: AppContainer) -> str:
    return f"{container.settings.api_prefix}/auth"


def set_refresh_cookie(response: Response, container: AppContainer, token: str) -> None:
    response.set_cookie(REFRESH_COOKIE, token, max_age=container.settings.refresh_token_days * 86400, httponly=True,
                        secure=container.settings.cookie_secure, samesite="strict", path=_cookie_path(container))


@router.post("/login")
async def login(body: LoginBody, request: Request, session: AsyncSession = Depends(get_session),
                container: AppContainer = Depends(get_container)):
    pair, user, tenant = await AuthService(container, session).login(body.email, body.password, client_ip(request))
    response = JSONResponse({"accessToken": pair.access_token, "expiresIn": pair.expires_in,
                             "mustChangePassword": bool(user.must_change_password), "user": user_dto(user, tenant)})
    set_refresh_cookie(response, container, pair.refresh_token)
    return response


@router.post("/refresh")
async def refresh(request: Request, session: AsyncSession = Depends(get_session),
                  container: AppContainer = Depends(get_container)):
    token = await AuthService(container, session).refresh(request.cookies.get(REFRESH_COOKIE))
    return {"accessToken": token, "expiresIn": container.settings.access_token_minutes * 60}


@router.post("/logout", status_code=204)
async def logout(request: Request, claims: Claims = Depends(current_claims),
                 session: AsyncSession = Depends(get_session), container: AppContainer = Depends(get_container)):
    await AuthService(container, session).logout(claims.user_id, claims.tenant_id, claims.jti, claims.exp,
                                                 request.cookies.get(REFRESH_COOKIE), client_ip(request))
    response = Response(status_code=204)
    response.delete_cookie(REFRESH_COOKIE, path=_cookie_path(container))
    return response


@router.get("/me")
async def me(claims: Claims = Depends(current_claims), session: AsyncSession = Depends(get_session),
             container: AppContainer = Depends(get_container)):
    return await AuthService(container, session).me(claims.user_id)


@router.post("/password/change", status_code=204)
async def change_password(body: PasswordChangeBody, request: Request, claims: Claims = Depends(current_claims),
                          session: AsyncSession = Depends(get_session),
                          container: AppContainer = Depends(get_container)):
    await AuthService(container, session).change_password(claims.user_id, body.currentPassword, body.newPassword,
                                                          client_ip(request))
    return Response(status_code=204)
