"""StorageClient: Amazon S3 access for the model artifacts (STORAGE_BACKEND=s3, used on
EC2 with the instance role). LocalStorageClient (local_storage.py) has the same
interface for the laptop stack.

Keys follow models/{modelId}/{version}/model.pt in the scicp-models bucket.
presign_put/presign_get return URLs the browser uses directly; head() reads
size and the SHA-256 checksum S3 stores when the upload carried one, and
sha256_of() streams the object to compute it when it did not. boto3 is
synchronous, so every call runs in a worker thread.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
from dataclasses import dataclass
from typing import Optional

from ..config import Settings

log = logging.getLogger("scicp.storage")


@dataclass
class HeadInfo:
    size: int
    sha256: Optional[str]  # hex, when S3 knows it


class StorageClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bucket = settings.s3_models_bucket
        self._client = None
        self._public_client = None

    @staticmethod
    def artifact_key(model_id: int, version: str) -> str:
        return f"models/{model_id}/{version}/model.pt"

    def artifact_uri(self, model_id: int, version: str) -> str:
        return f"s3://{self.bucket}/{self.artifact_key(model_id, version)}"

    # -- boto3 clients (lazy) -----------------------------------------------
    def _make(self, endpoint: Optional[str]):
        import boto3
        from botocore.config import Config
        kwargs = {"region_name": self.settings.s3_region,
                  "config": Config(signature_version="s3v4", connect_timeout=3, read_timeout=10,
                                   retries={"max_attempts": 1})}
        if endpoint:
            kwargs["endpoint_url"] = endpoint
        if self.settings.s3_access_key:
            kwargs["aws_access_key_id"] = self.settings.s3_access_key
            kwargs["aws_secret_access_key"] = self.settings.s3_secret_key
        return boto3.client("s3", **kwargs)

    @property
    def client(self):
        if self._client is None:
            self._client = self._make(self.settings.s3_endpoint_url or None)
        return self._client

    @property
    def public_client(self):
        """Signs URLs with the endpoint the browser can reach (S3_PUBLIC_ENDPOINT_URL when the
        API reaches the bucket through a private endpoint; normally the regional S3 endpoint)."""
        if self._public_client is None:
            endpoint = self.settings.s3_public_endpoint_url or self.settings.s3_endpoint_url or None
            self._public_client = self._make(endpoint)
        return self._public_client

    # -- operations ------------------------------------------------------------
    async def presign_put(self, key: str) -> str:
        return await asyncio.to_thread(
            self.public_client.generate_presigned_url, "put_object",
            Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=self.settings.presign_seconds)

    async def presign_get(self, key: str) -> str:
        return await asyncio.to_thread(
            self.public_client.generate_presigned_url, "get_object",
            Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=self.settings.presign_seconds)

    def _head_sync(self, key: str) -> Optional[HeadInfo]:
        from botocore.exceptions import ClientError
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key, ChecksumMode="ENABLED")
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        sha = None
        checksum = response.get("ChecksumSHA256")
        if checksum:
            sha = base64.b64decode(checksum).hex()
        elif response.get("Metadata", {}).get("sha256"):
            sha = response["Metadata"]["sha256"].lower()
        return HeadInfo(size=int(response["ContentLength"]), sha256=sha)

    async def head(self, key: str) -> Optional[HeadInfo]:
        return await asyncio.to_thread(self._head_sync, key)

    def _sha256_sync(self, key: str) -> str:
        digest = hashlib.sha256()
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        for chunk in iter(lambda: body.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()

    async def sha256_of(self, key: str) -> str:
        return await asyncio.to_thread(self._sha256_sync, key)

    async def verify(self, key: str, size_bytes: Optional[int], sha256: Optional[str]) -> bool:
        """True when the object exists with that size and SHA-256."""
        head = await self.head(key)
        if head is None or size_bytes is None or head.size != int(size_bytes):
            return False
        actual = head.sha256 or await self.sha256_of(key)
        return bool(sha256) and actual.lower() == sha256.lower()

    def _put_sync(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data,
                               Metadata={"sha256": hashlib.sha256(data).hexdigest()})

    async def put_bytes(self, key: str, data: bytes) -> None:
        """Used by the seed to upload the dummy artifacts."""
        await asyncio.to_thread(self._put_sync, key, data)

    def _ensure_bucket_sync(self) -> bool:
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code not in ("404", "NoSuchBucket", "NotFound"):
                return False
        except BotoCoreError:
            return False
        try:
            params = {"Bucket": self.bucket}
            if not self.settings.s3_endpoint_url and self.settings.s3_region != "us-east-1":
                params["CreateBucketConfiguration"] = {"LocationConstraint": self.settings.s3_region}
            self.client.create_bucket(**params)
            return True
        except (ClientError, BotoCoreError) as exc:
            log.warning("Cannot create bucket %s: %s", self.bucket, exc)
            return False

    async def ensure_bucket(self) -> bool:
        """True when the bucket is reachable (created when missing)."""
        try:
            return await asyncio.to_thread(self._ensure_bucket_sync)
        except Exception as exc:  # noqa: BLE001 - any network failure means "not reachable"
            log.warning("S3 not reachable: %s", exc)
            return False
