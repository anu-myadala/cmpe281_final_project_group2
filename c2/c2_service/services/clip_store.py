"""Where the clip bytes live (Section 3.3.3 of the C2 design).

    LocalClipStore  a directory (the "clips" volume of docker-compose.c2.yml): laptop and default EC2
    S3ClipStore     the scicp-clips bucket (C2_CLIP_BACKEND=s3), through the instance role

Both use the same object keys: tenants/{tenantId}/vehicles/{vehicleId}/YYYY/MM/DD/{clipId}.wav
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional, Protocol


class ClipStore(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def get(self, key: str) -> Optional[bytes]: ...
    async def delete(self, key: str) -> None: ...


class LocalClipStore:
    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("bad clip key")
        return path

    async def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    async def get(self, key: str) -> Optional[bytes]:
        path = self._path(key)
        return path.read_bytes() if path.exists() else None

    async def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class S3ClipStore:
    def __init__(self, bucket: str, region: str) -> None:
        import boto3  # only needed on EC2 with C2_CLIP_BACKEND=s3
        self.bucket = bucket
        self.client = boto3.client("s3", region_name=region)

    async def put(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(self.client.put_object, Bucket=self.bucket, Key=key, Body=data,
                                ContentType="audio/wav")

    async def get(self, key: str) -> Optional[bytes]:
        def _get():
            try:
                return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            except self.client.exceptions.NoSuchKey:
                return None
        return await asyncio.to_thread(_get)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)


def make_clip_store(settings) -> ClipStore:
    if settings.c2_clip_backend == "s3":
        return S3ClipStore(settings.s3_clips_bucket, settings.s3_region)
    return LocalClipStore(settings.c2_clip_dir)
