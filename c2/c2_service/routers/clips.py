"""Clip upload, playback, deletion and the internal fetch (Sections 2.5 and 2.7 of the C2 design)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from ..auth import Caller, bearer_token, require_roles
from ..deps import any_caller, get_services, get_session_factory, service_caller
from ..errors import NotFound
from ..models import Clip, Vehicle

router = APIRouter(tags=["clips"])
internal = APIRouter(prefix="/internal", tags=["internal"])

DATA_READERS = ("OWNER", "FLEET_MANAGER", "IOT_ENGINEER", "SERVICE_STAFF", "SYS_ADMIN")
DATA_WRITERS = ("OWNER", "FLEET_MANAGER", "IOT_ENGINEER")


@router.post("/ingest/clips")
async def ingest_clip(request: Request, file: UploadFile = File(...), deviceId: str = Form(...),
                      eventId: str = Form(default=""), ts: str = Form(default=""),
                      idempotency_key: str = Header(default="", alias="Idempotency-Key"),
                      services: Any = Depends(get_services)):
    """The edge agent's upload (device token). 201 a new clip, 200 the stored one for a repeated eventId."""
    token = bearer_token(request)
    data = await file.read(services.settings.c2_max_clip_bytes + 1)   # the size is checked after the token (FC1)
    status, body = await services.clips.ingest(token, data, deviceId, eventId or idempotency_key, ts or None)
    return JSONResponse(status_code=status, content=body)


async def _clip_in_scope(session, caller: Caller, clip_id: str) -> tuple:
    clip = await session.get(Clip, clip_id)
    if clip is None or (caller.tenant_id is not None and clip.tenant_id != caller.tenant_id):
        raise NotFound("Clip not found")
    vehicle = await session.get(Vehicle, clip.vehicle_id)
    return clip, vehicle


@router.get("/clips/{clip_id}/url")
async def clip_url(clip_id: str, caller: Caller = Depends(any_caller), session_factory: Any = Depends(get_session_factory),
                   services: Any = Depends(get_services)):
    require_roles(caller, *DATA_READERS)
    async with session_factory() as session:
        clip, _ = await _clip_in_scope(session, caller, clip_id)
    if clip.state != "STORED":
        from ..errors import Gone
        raise Gone(f"The clip was {clip.state.lower()}")
    return services.clips.signed_url(clip)


@router.get("/clips/{clip_id}/file")
async def clip_file(clip_id: str, exp: int = Query(...), sig: str = Query(...), services: Any = Depends(get_services)):
    """The signed playback link (no bearer header: an <audio> element cannot send one)."""
    data = await services.clips.signed_file(clip_id, exp, sig)
    return Response(content=data, media_type="audio/wav", headers={"Cache-Control": "private, max-age=600"})


@router.delete("/clips/{clip_id}", status_code=204)
async def delete_clip(clip_id: str, caller: Caller = Depends(any_caller), session_factory: Any = Depends(get_session_factory),
                      services: Any = Depends(get_services)):
    require_roles(caller, *DATA_WRITERS)
    async with session_factory() as session:
        clip, vehicle = await _clip_in_scope(session, caller, clip_id)
        await services.clips.delete(session, caller, clip, vehicle)
        await session.commit()


@internal.get("/clips/{clip_id}")
async def internal_clip(clip_id: str, _service: dict = Depends(service_caller), services: Any = Depends(get_services)):
    """For the C4 worker (agreed with the C3 design, its Table 10): 200 audio/wav, 5 s mono 16 kHz."""
    data, headers = await services.clips.fetch(clip_id)
    return Response(content=data, media_type="audio/wav", headers=headers)


@internal.post("/jobs/retention")
async def run_retention(_service: dict = Depends(service_caller), services: Any = Depends(get_services)):
    """Runs the daily retention job now (demo and tests)."""
    return {"expired": await services.clips.run_retention()}
