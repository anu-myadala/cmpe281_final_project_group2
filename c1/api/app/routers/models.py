"""ModelRouter: the C4 registry API hosted by C1 (/models/..., /events/{eventRef}/feedback)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..container import AppContainer
from ..core.rbac import RbacGuard
from ..core.security import Claims
from ..deps import client_ip, get_container, get_session
from ..schemas import ActivateBody, FeedbackBody, ModelCreateBody, RollbackBody, VersionCompleteBody, VersionCreateBody
from ..services import ModelRegistryService

router = APIRouter(tags=["models"])

registry_read = RbacGuard.requires("IOT_ENGINEER", "SERVICE_STAFF", "SYS_ADMIN")
registry_write = RbacGuard.requires("SERVICE_STAFF", "SYS_ADMIN")
feedback_roles = RbacGuard.requires("OWNER", "FLEET_MANAGER", "SERVICE_STAFF", "SYS_ADMIN")


@router.get("/models")
async def list_models(_: Claims = Depends(registry_read), session: AsyncSession = Depends(get_session),
                      container: AppContainer = Depends(get_container)):
    return await ModelRegistryService(container, session).list_models()


@router.post("/models", status_code=201)
async def create_model(body: ModelCreateBody, request: Request, claims: Claims = Depends(registry_write),
                       session: AsyncSession = Depends(get_session), container: AppContainer = Depends(get_container)):
    return await ModelRegistryService(container, session).create_model(body.name, body.task, claims.user_id,
                                                                       client_ip(request))


@router.get("/models/{model_id}/versions")
async def list_versions(model_id: int, _: Claims = Depends(registry_read), session: AsyncSession = Depends(get_session),
                        container: AppContainer = Depends(get_container)):
    return await ModelRegistryService(container, session).list_versions(model_id)


@router.post("/models/{model_id}/versions", status_code=201)
async def begin_upload(model_id: int, body: VersionCreateBody, request: Request,
                       claims: Claims = Depends(registry_write), session: AsyncSession = Depends(get_session),
                       container: AppContainer = Depends(get_container)):
    return await ModelRegistryService(container, session).begin_upload(model_id, body.version, body.notes,
                                                                       claims.user_id, client_ip(request))


@router.post("/models/{model_id}/versions/{version}/complete")
async def complete_upload(model_id: int, version: str, body: VersionCompleteBody, request: Request,
                          claims: Claims = Depends(registry_write), session: AsyncSession = Depends(get_session),
                          container: AppContainer = Depends(get_container)):
    return await ModelRegistryService(container, session).complete_upload(
        model_id, version, body.sha256, body.sizeBytes, body.metrics, claims.user_id, client_ip(request))


@router.post("/models/{model_id}/versions/{version}/activate")
async def activate(model_id: int, version: str, body: ActivateBody, request: Request,
                   claims: Claims = Depends(registry_write), session: AsyncSession = Depends(get_session),
                   container: AppContainer = Depends(get_container)):
    return await ModelRegistryService(container, session).activate(model_id, version, body.force, body.note,
                                                                   claims.user_id, client_ip(request))


@router.post("/models/{model_id}/rollback")
async def rollback(model_id: int, body: RollbackBody, request: Request, claims: Claims = Depends(registry_write),
                   session: AsyncSession = Depends(get_session), container: AppContainer = Depends(get_container)):
    return await ModelRegistryService(container, session).rollback(model_id, body.note, claims.user_id,
                                                                   client_ip(request))


@router.get("/models/{model_id}/versions/{version}/download")
async def download(model_id: int, version: str, _: Claims = Depends(registry_read),
                   session: AsyncSession = Depends(get_session), container: AppContainer = Depends(get_container)):
    url = await ModelRegistryService(container, session).download_url(model_id, version)
    return RedirectResponse(url, status_code=302)


@router.get("/models/{model_id}/feedback/export")
async def export_feedback(model_id: int, versionId: Optional[int] = None, format: str = "csv",
                          _: Claims = Depends(registry_read), session: AsyncSession = Depends(get_session),
                          container: AppContainer = Depends(get_container)):
    body = await ModelRegistryService(container, session).export_feedback(model_id, versionId)
    name = f"feedback_model{model_id}" + (f"_v{versionId}" if versionId else "") + ".csv"
    return Response(body, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/events/{event_ref}/feedback", status_code=201)
async def feedback(event_ref: str, body: FeedbackBody, request: Request, claims: Claims = Depends(feedback_roles),
                   session: AsyncSession = Depends(get_session), container: AppContainer = Depends(get_container)):
    return await ModelRegistryService(container, session).record_feedback(
        event_ref, body, claims.user_id, claims.role, claims.tenant_id, client_ip(request))
