"""LocalStorageClient: the model artifacts on a local directory (a named Docker
volume in the compose stack) instead of an S3 bucket.

It has the same interface as StorageClient, so ModelRegistryService and the
seed do not know which backend they talk to. The presigned URLs are relative
API paths signed with HMAC-SHA256 (JWT_SECRET):

    /api/v1/storage/objects/<key>?op=put|get&exp=<unix seconds>&sig=<hex>

Relative URLs work behind nginx on any host name (laptop, EC2) because the
browser resolves them against the page origin. The upload and download are
served by app.routers.storage. Every object has a sidecar file
"<object>.sha256" with its hex digest, written when the upload finishes, so
head() can answer with the checksum without reading the object again.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import AsyncIterator, Optional, Tuple
from urllib.parse import quote, urlencode

from ..config import Settings
from .storage_client import HeadInfo

log = logging.getLogger("scicp.storage")

SIDECAR_SUFFIX = ".sha256"
CHUNK_BYTES = 1024 * 1024


class InvalidKey(ValueError):
    """The key would leave the storage directory or clash with a sidecar file."""


class LocalStorageClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bucket = settings.s3_models_bucket
        self.root = Path(settings.storage_dir)
        self._secret = settings.jwt_secret.encode()

    @staticmethod
    def artifact_key(model_id: int, version: str) -> str:
        return f"models/{model_id}/{version}/model.pt"

    def artifact_uri(self, model_id: int, version: str) -> str:
        return f"local://{self.bucket}/{self.artifact_key(model_id, version)}"

    # -- paths -------------------------------------------------------------------------
    def path_for(self, key: str) -> Path:
        """Absolute path of an object; refuses keys that escape the root (.., absolute paths)."""
        parts = [p for p in key.split("/") if p]
        if not parts or any(p in (".", "..") for p in parts) or "\\" in key or "\x00" in key:
            raise InvalidKey(key)
        if parts[-1].endswith(SIDECAR_SUFFIX) or parts[-1].startswith(".tmp-"):
            raise InvalidKey(key)
        root = self.root.resolve()
        path = root.joinpath(*parts).resolve()
        if root != path and root not in path.parents:
            raise InvalidKey(key)
        return path

    @staticmethod
    def _sidecar(path: Path) -> Path:
        return path.with_name(path.name + SIDECAR_SUFFIX)

    # -- signed URLs ----------------------------------------------------------------------
    def sign(self, op: str, key: str, exp: int) -> str:
        message = f"{op}\n{key}\n{exp}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def check_signature(self, op: str, key: str, exp: Optional[int], sig: Optional[str],
                        now: Optional[float] = None) -> bool:
        if op not in ("put", "get") or exp is None or not sig:
            return False
        if int(exp) < int(now if now is not None else time.time()):
            return False
        return hmac.compare_digest(self.sign(op, key, int(exp)), sig)

    def _presign(self, op: str, key: str) -> str:
        exp = int(time.time()) + int(self.settings.presign_seconds)
        query = urlencode({"op": op, "exp": exp, "sig": self.sign(op, key, exp)})
        return f"{self.settings.api_prefix}/storage/objects/{quote(key)}?{query}"

    async def presign_put(self, key: str) -> str:
        return self._presign("put", key)

    async def presign_get(self, key: str) -> str:
        return self._presign("get", key)

    # -- reads ------------------------------------------------------------------------------
    def _head_sync(self, key: str) -> Optional[HeadInfo]:
        path = self.path_for(key)
        if not path.is_file():
            return None
        sha = None
        sidecar = self._sidecar(path)
        if sidecar.is_file():
            sha = sidecar.read_text().strip().lower() or None
        return HeadInfo(size=path.stat().st_size, sha256=sha)

    async def head(self, key: str) -> Optional[HeadInfo]:
        return await asyncio.to_thread(self._head_sync, key)

    def _sha256_sync(self, key: str) -> str:
        digest = hashlib.sha256()
        with open(self.path_for(key), "rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
                digest.update(chunk)
        return digest.hexdigest()

    async def sha256_of(self, key: str) -> str:
        return await asyncio.to_thread(self._sha256_sync, key)

    async def verify(self, key: str, size_bytes: Optional[int], sha256: Optional[str]) -> bool:
        """True when the object exists with that size and SHA-256 (same rule as StorageClient)."""
        head = await self.head(key)
        if head is None or size_bytes is None or head.size != int(size_bytes):
            return False
        actual = head.sha256 or await self.sha256_of(key)
        return bool(sha256) and actual.lower() == sha256.lower()

    # -- writes -----------------------------------------------------------------------------
    def _open_temp(self, path: Path) -> Tuple[int, str]:
        path.parent.mkdir(parents=True, exist_ok=True)
        return tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))

    def _commit(self, tmp_name: str, path: Path, sha: str) -> None:
        """Rename into place, then write the sidecar (also through a rename)."""
        os.replace(tmp_name, path)
        sidecar = self._sidecar(path)
        fd, side_tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
        with os.fdopen(fd, "w") as handle:
            handle.write(sha + "\n")
        os.replace(side_tmp, sidecar)

    async def write_stream(self, key: str, chunks: AsyncIterator[bytes], max_bytes: Optional[int] = None
                           ) -> HeadInfo:
        """Streams an upload to a temporary file next to the target and renames it when complete,
        so a reader never sees a half-written artifact. Raises OverflowError past max_bytes."""
        path = self.path_for(key)
        fd, tmp_name = await asyncio.to_thread(self._open_temp, path)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as handle:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise OverflowError(f"object larger than {max_bytes} bytes")
                    digest.update(chunk)
                    await asyncio.to_thread(handle.write, chunk)
            sha = digest.hexdigest()
            await asyncio.to_thread(self._commit, tmp_name, path, sha)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return HeadInfo(size=size, sha256=sha)

    async def put_bytes(self, key: str, data: bytes) -> None:
        """Used by the seed to store the dummy artifacts."""
        async def one_chunk():
            yield data
        await self.write_stream(key, one_chunk())

    def _ensure_sync(self) -> bool:
        self.root.mkdir(parents=True, exist_ok=True)
        probe = self.root / ".tmp-probe"
        probe.write_bytes(b"")
        probe.unlink()
        return True

    async def ensure_bucket(self) -> bool:
        """True when the storage directory exists and is writable."""
        try:
            return await asyncio.to_thread(self._ensure_sync)
        except OSError as exc:
            log.warning("Storage directory %s not usable: %s", self.root, exc)
            return False


def make_storage(settings: Settings):
    """The artifact storage of STORAGE_BACKEND: LocalStorageClient (local) or StorageClient (s3)."""
    if settings.storage_backend == "s3":
        from .storage_client import StorageClient
        return StorageClient(settings)
    return LocalStorageClient(settings)
