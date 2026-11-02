"""Local object storage (STORAGE_BACKEND=local): the signed PUT/GET routes behind
LocalStorageClient's presigned URLs, the sidecar checksum, and the model upload
and download going through them end to end."""
from __future__ import annotations

import hashlib
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from app.clients import LocalStorageClient
from conftest import test_settings as make_settings

MODEL = 1


@pytest.fixture
def local_storage(container, tmp_path):
    """Swap the in-memory fake for a real LocalStorageClient on a temporary directory."""
    original = container.storage
    storage = LocalStorageClient(make_settings(storage_dir=str(tmp_path / "objects")))
    container.storage = storage
    yield storage
    container.storage = original


async def test_signed_put_then_get_roundtrip(client, local_storage):
    assert await local_storage.ensure_bucket()
    key = "models/1/7.0.0/model.pt"
    data = b"weights " * 4096
    put_url = await local_storage.presign_put(key)
    assert put_url.startswith("/api/v1/storage/objects/models/1/7.0.0/model.pt?")
    assert set(parse_qs(urlsplit(put_url).query)) == {"op", "exp", "sig"}

    put = await client.put(put_url, content=data, headers={"Content-Type": "application/octet-stream"})
    assert put.status_code == 200, put.text
    digest = hashlib.sha256(data).hexdigest()
    assert put.json() == {"key": key, "sizeBytes": len(data), "sha256": digest}

    # The sidecar answers head() without reading the object; verify() checks size and digest.
    head = await local_storage.head(key)
    assert head is not None and head.size == len(data) and head.sha256 == digest
    assert (local_storage.root / "models/1/7.0.0/model.pt.sha256").read_text().strip() == digest
    assert await local_storage.verify(key, len(data), digest)
    assert not await local_storage.verify(key, len(data), "0" * 64)
    assert not await local_storage.verify(key, len(data) + 1, digest)
    assert await local_storage.sha256_of(key) == digest
    assert not list(local_storage.root.rglob(".tmp-*"))  # the temporary file was renamed

    got = await client.get(await local_storage.presign_get(key))
    assert got.status_code == 200
    assert got.content == data
    assert got.headers["content-disposition"].startswith("attachment")
    assert got.headers["etag"] == f'"{digest}"'


async def test_bad_signature_is_refused_with_403(client, local_storage):
    key = "models/1/7.0.1/model.pt"
    good = await local_storage.presign_put(key)
    query = parse_qs(urlsplit(good).query)
    exp = query["exp"][0]

    tampered = good.replace(query["sig"][0], "0" * 64)
    response = await client.put(tampered, content=b"x")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"

    # A signature of one key does not open another key.
    other = good.replace("7.0.1", "7.0.2")
    assert (await client.put(other, content=b"x")).status_code == 403
    # A PUT signature is not a GET signature, and nothing at all is refused too.
    assert (await client.get(good)).status_code == 403
    assert (await client.put(f"/api/v1/storage/objects/{key}", content=b"x")).status_code == 403
    # Nothing was written by the refused calls.
    assert await local_storage.head(key) is None
    assert exp.isdigit()


async def test_expired_signature_is_refused_with_403(client, local_storage):
    key = "models/1/7.0.3/model.pt"
    exp = int(time.time()) - 5
    sig = local_storage.sign("put", key, exp)
    response = await client.put(f"/api/v1/storage/objects/{key}?op=put&exp={exp}&sig={sig}", content=b"x")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert await local_storage.head(key) is None


async def test_keys_outside_the_storage_directory_are_refused(client, local_storage):
    for key in ("models/../../etc/passwd", "models/1/7.0.0/model.pt.sha256"):
        exp = int(time.time()) + 60
        sig = local_storage.sign("put", key, exp)
        response = await client.put(f"/api/v1/storage/objects/{key}?op=put&exp={exp}&sig={sig}", content=b"x")
        assert response.status_code in (403, 404), key


async def test_model_upload_and_download_through_local_storage(client, auth, local_storage):
    staff = await auth.headers("staff")
    data = b"local storage artifact 9.1.0\n" * 300
    digest = hashlib.sha256(data).hexdigest()
    begun = await client.post(f"/api/v1/models/{MODEL}/versions", headers=staff,
                              json={"version": "9.1.0", "sizeBytes": len(data), "sha256": digest})
    assert begun.status_code == 201, begun.text
    upload = begun.json()["upload"]
    assert upload["url"].startswith("/api/v1/storage/objects/models/1/9.1.0/model.pt?op=put")
    assert (await client.put(upload["url"], content=data)).status_code == 200

    done = await client.post(f"/api/v1/models/{MODEL}/versions/9.1.0/complete", headers=staff,
                             json={"sha256": digest, "sizeBytes": len(data),
                                   "metrics": {"macroF1": 0.85, "accuracy": 0.88}})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "REGISTERED"
    assert done.json()["artifactUri"] == "local://scicp-models/models/1/9.1.0/model.pt"

    # The download answers 302 with a relative Location the browser resolves against the page origin.
    redirect = await client.get(f"/api/v1/models/{MODEL}/versions/9.1.0/download", headers=staff)
    assert redirect.status_code == 302
    location = redirect.headers["location"]
    assert location.startswith("/api/v1/storage/objects/models/1/9.1.0/model.pt?op=get")
    fetched = await client.get(location)
    assert fetched.status_code == 200 and fetched.content == data
