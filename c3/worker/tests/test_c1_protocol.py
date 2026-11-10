"""The calls to C1, with the shapes of stubs/worker.py (GET /internal/models/active, the presigned download,
POST /internal/worker/status) and its timings (wake on model.activated, poll, 5 s retry with no model)."""
import asyncio
import threading

import jwt

import fakes
from c4_worker.model_manager import ModelManager

STATUS_KEYS = {"workerId", "modelId", "modelVersion", "sha256", "state", "loadedAt", "detail"}


def head_bytes(tmp_path):
    return fakes.write_head(tmp_path / "h.pt").read_bytes()


async def test_active_call_shape(manager, services, tmp_path):
    services.set_active(head_bytes(tmp_path))
    await manager.sync("startup")
    req = services.requests[0]
    assert req.method == "GET" and str(req.url) == f"{fakes.C1}/internal/models/active?name=sound-classifier"
    claims = jwt.decode(req.headers["authorization"].split()[1], fakes.SECRET, algorithms=["HS256"])
    assert claims["role"] == "SERVICE" and claims["sub"] == "c4-worker"
    assert claims["exp"] - claims["iat"] == 24 * 3600 and claims["jti"]


async def test_status_bodies_match_the_stub(manager, services, tmp_path):
    info = services.set_active(head_bytes(tmp_path), "1.3.0", model_id=1)
    await manager.sync("startup")
    loading, ready = services.status_reports
    assert set(loading) == STATUS_KEYS and set(ready) == STATUS_KEYS
    assert loading == {"workerId": "c4-worker-1", "modelId": 1, "modelVersion": "1.3.0", "sha256": None,
                       "state": "LOADING", "loadedAt": None,
                       "detail": "downloading sound-classifier-1.3.0 (startup)"}
    assert ready["state"] == "READY" and ready["sha256"] == info["sha256"] and ready["modelVersion"] == "1.3.0"
    assert ready["loadedAt"].endswith("Z") and ready["detail"].startswith("serving sound-classifier-1.3.0")
    posts = [r for r in services.requests if r.url.path.endswith("/internal/worker/status")]
    assert all(r.method == "POST" and r.headers["authorization"].startswith("Bearer ") for r in posts)


async def test_relative_download_url_resolves_against_c1_host(manager, services, tmp_path):
    services.set_active(head_bytes(tmp_path), "1.3.0")
    await manager.sync("startup")
    download = services.requests[2]
    assert str(download.url).startswith("http://api:8000/api/v1/storage/objects/models/sound-classifier/1.3.0/")
    assert "op=get" in str(download.url)


async def test_absolute_download_url_is_used_as_is(manager, services, tmp_path):
    services.set_active(head_bytes(tmp_path), "1.3.0", url="https://bucket.s3.amazonaws.com/m/1.3.0/model.pt?X-Amz=1")
    services.artifacts["/m/1.3.0/model.pt"] = services.artifacts.pop("https://bucket.s3.amazonaws.com/m/1.3.0/model.pt")
    await manager.sync("startup")
    assert str(services.requests[2].url).startswith("https://bucket.s3.amazonaws.com/m/1.3.0/model.pt")
    assert services.states() == ["LOADING", "READY"]


async def test_same_version_is_a_ready_heartbeat_without_download(manager, services, tmp_path):
    services.set_active(head_bytes(tmp_path), "1.3.0")
    await manager.sync("startup")
    n = len(services.requests)
    await manager.sync("poll")
    assert services.states() == ["LOADING", "READY", "READY"]
    assert [r.url.path for r in services.requests[n:]] == ["/api/v1/internal/models/active",
                                                           "/api/v1/internal/worker/status"]
    assert services.status_reports[-1]["detail"] == "serving sound-classifier-1.3.0"


async def test_size_mismatch_is_failed(manager, services, tmp_path):
    data = head_bytes(tmp_path)
    services.set_active(data, "1.3.0", size=len(data) + 1)
    await manager.sync("startup")
    assert services.states() == ["LOADING", "FAILED"] and manager.current is None


async def test_download_error_is_failed(manager, services, tmp_path):
    services.set_active(head_bytes(tmp_path), "1.3.0")
    services.artifacts.clear()                       # presigned URL answers 404
    await manager.sync("startup")
    assert services.states() == ["LOADING", "FAILED"]
    assert services.status_reports[-1]["detail"].startswith("download failed")


async def test_seed_dummy_artifact_reports_failed_within_c1_limit(manager, services):
    # seed/seed.py's 1.1.0 artifact: text, not a torch file; torch's error message is over 500 characters
    services.set_active(b"SCICP dummy model artifact sound-classifier 1.1.0 (CLAP + head)\n" * 512, "1.1.0")
    await manager.sync("startup")
    assert services.states() == ["LOADING", "FAILED"] and manager.state == "NO_MODEL"
    detail = services.status_reports[-1]["detail"]
    assert detail.startswith("load failed: unreadable artifact") and len(detail) <= 500 and "\n" not in detail


async def test_long_detail_is_truncated_to_500(manager, services, http):
    await manager.c1.report(http, "FAILED", 1, "9.9.9", detail="x" * 900)
    assert len(services.status_reports[-1]["detail"]) == 500


async def test_no_active_version_reports_nothing(manager, services):
    assert await manager.sync("startup") is False
    assert services.status_reports == [] and manager.state == "NO_MODEL"


async def test_run_retries_every_5s_without_model_and_wakes_on_activation(manager, services, tmp_path, monkeypatch):
    timeouts = []
    real_wait_for = asyncio.wait_for

    async def fake_wait_for(aw, timeout):
        timeouts.append(timeout)
        return await real_wait_for(aw, timeout=0.05 if timeout >= 5 else timeout)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    task = asyncio.create_task(manager.run(poll_s=30.0))
    for _ in range(100):
        if len(timeouts) >= 2:
            break
        await asyncio.sleep(0.01)
    assert timeouts[:2] == [5.0, 5.0]                    # no ACTIVE version: retry after 5 s

    services.set_active(head_bytes(tmp_path), "1.3.0")
    # an activation arrives on paho's network thread
    threading.Thread(target=manager.wake, args=("MQTT scicp/v1/platform/model/activated 1.3.0",)).start()
    for _ in range(200):
        if manager.current is not None:
            break
        await asyncio.sleep(0.01)
    for _ in range(100):
        if timeouts[-1] == 30.0:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    assert manager.model_version == "sound-classifier-1.3.0"
    assert timeouts[-1] == 30.0                           # with a model: the 30 s poll
    assert "LOADING" in services.states() and "READY" in services.states()


async def test_c1_down_is_retried(manager, http, token, tmp_path):
    import httpx
    from c4_worker.clients import C1Client

    async def down(request):
        raise httpx.ConnectError("refused")
    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as client:
        m = ModelManager(C1Client(fakes.C1, token, "c4-worker-1", "sound-classifier"), manager.encoder,
                         tmp_path, client)
        task = asyncio.create_task(m.run(poll_s=30.0))
        await asyncio.sleep(0.05)
        assert not task.done()                            # the loop survives C1 being down
        task.cancel()
