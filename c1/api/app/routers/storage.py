"""StorageRouter: the upload and download endpoints behind the presigned URLs of
LocalStorageClient (STORAGE_BACKEND=local).

    PUT /api/v1/storage/objects/{key}?op=put&exp=&sig=   body = the artifact bytes
    GET /api/v1/storage/objects/{key}?op=get&exp=&sig=   -> the bytes (attachment)

The signature is the only credential, as with an S3 presigned URL: the browser
sends no bearer token, so a 403 envelope answers a missing, wrong or expired
signature. With STORAGE_BACKEND=s3 the routes answer 404 (the bucket serves the
presigned URLs itself).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from ..clients import LocalStorageClient
from ..clients.local_storage import InvalidKey
from ..container import AppContainer
from ..deps import get_container
from ..errors import ApiError, Forbidden, NotFound

router = APIRouter(prefix="/storage", tags=["storage"])

# Same limit as client_max_body_size in c1/web/nginx/default.conf.
MAX_OBJECT_BYTES = 512 * 1024 * 1024


def _local(container: AppContainer) -> LocalStorageClient:
    storage = container.storage
    if not isinstance(storage, LocalStorageClient):
        raise NotFound("Local object storage is not enabled (STORAGE_BACKEND=s3)")
    return storage


def _checked(storage: LocalStorageClient, op_expected: str, key: str, op: Optional[str], exp: Optional[int],
             sig: Optional[str]) -> None:
    if op != op_expected or not storage.check_signature(op, key, exp, sig):
        raise Forbidden("Invalid or expired storage signature")
    try:
        storage.path_for(key)
    except InvalidKey:
        raise Forbidden("Invalid object key")


@router.put("/objects/{key:path}")
async def put_object(key: str, request: Request, op: Optional[str] = Query(default=None),
                     exp: Optional[int] = Query(default=None), sig: Optional[str] = Query(default=None),
                     container: AppContainer = Depends(get_container)):
    storage = _local(container)
    _checked(storage, "put", key, op, exp, sig)
    try:
        info = await storage.write_stream(key, request.stream(), max_bytes=MAX_OBJECT_BYTES)
    except OverflowError:
        raise ApiError("Object larger than 512 MB", status=413, code="VALIDATION")
    # S3 answers 200 with an ETag; the web app only looks at the status.
    return JSONResponse({"key": key, "sizeBytes": info.size, "sha256": info.sha256},
                        headers={"ETag": f'"{info.sha256}"'})


@router.get("/objects/{key:path}")
async def get_object(key: str, op: Optional[str] = Query(default=None), exp: Optional[int] = Query(default=None),
                     sig: Optional[str] = Query(default=None), container: AppContainer = Depends(get_container)):
    storage = _local(container)
    _checked(storage, "get", key, op, exp, sig)
    path = storage.path_for(key)
    if not path.is_file():
        raise NotFound("Object not found")
    head = await storage.head(key)
    headers = {"Cache-Control": "private, no-store"}
    if head is not None and head.sha256:
        headers["ETag"] = f'"{head.sha256}"'
    return FileResponse(path, media_type="application/octet-stream", filename=path.name,
                        content_disposition_type="attachment", headers=headers)
