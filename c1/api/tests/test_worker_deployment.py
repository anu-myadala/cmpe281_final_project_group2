"""Model deployment loop: GET /internal/models/active and POST /internal/worker/status
for the C4 classifier worker (service token only), GET /models/{id}/deployment for the
registry roles, and the model.deployed message for the staff room."""
from __future__ import annotations

from app.core.timeutil import iso, utcnow
from app.models import ModelVersion
from sqlalchemy import select

MODEL = 1


async def _active_row(db) -> ModelVersion:
    return (await db.execute(select(ModelVersion).where(ModelVersion.model_id == MODEL,
                                                        ModelVersion.status == "ACTIVE"))).scalars().one()


async def test_active_model_for_the_worker(client, auth, service_headers, db):
    response = await client.get("/api/v1/internal/models/active", headers=service_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    active = await _active_row(db)
    assert body["modelId"] == MODEL and body["name"] == "sound-classifier"
    assert body["version"] == active.version and body["sha256"] == active.sha256
    assert body["sizeBytes"] == active.size_bytes and body["tag"] == f"sound-classifier-{active.version}"
    assert f"models/{MODEL}/{active.version}/model.pt" in body["downloadUrl"]
    # By name, and 404 for a model that does not exist.
    by_name = await client.get("/api/v1/internal/models/active", headers=service_headers,
                               params={"name": "sound-classifier"})
    assert by_name.status_code == 200 and by_name.json()["version"] == active.version
    missing = await client.get("/api/v1/internal/models/active", headers=service_headers, params={"modelId": 999})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"


async def test_internal_model_endpoints_need_the_service_token(client, auth):
    assert (await client.get("/api/v1/internal/models/active")).status_code == 401
    user = await auth.headers("admin")
    assert (await client.get("/api/v1/internal/models/active", headers=user)).status_code == 401
    report = {"workerId": "worker-x", "modelVersion": "1.1.0", "state": "READY"}
    assert (await client.post("/api/v1/internal/worker/status", json=report)).status_code == 401
    assert (await client.post("/api/v1/internal/worker/status", json=report, headers=user)).status_code == 401


async def test_worker_report_is_verified_stored_and_shown(client, auth, container, service_headers, db):
    active = await _active_row(db)
    received = []
    container.bus.subscribe("model.deployed", lambda topic, payload: received.append(payload))
    loaded_at = iso(utcnow())
    response = await client.post("/api/v1/internal/worker/status", headers=service_headers, json={
        "workerId": "worker-t1", "modelId": MODEL, "modelVersion": active.version, "sha256": active.sha256,
        "state": "READY", "loadedAt": loaded_at, "detail": "loaded in 40 ms"})
    assert response.status_code == 200, response.text
    assert response.json()["sha256Verified"] is True
    assert received and received[-1]["workerId"] == "worker-t1" and received[-1]["state"] == "READY"

    saved = await container.mongo["worker_status"].find_one({"_id": "worker-t1"})
    assert saved is not None and saved["modelVersion"] == active.version and saved["sha256Verified"] is True

    for who in ("iot", "staff", "admin"):
        page = await client.get(f"/api/v1/models/{MODEL}/deployment", headers=await auth.headers(who))
        assert page.status_code == 200, (who, page.text)
    body = page.json()
    assert body["activeVersion"] == active.version
    worker = next(w for w in body["workers"] if w["workerId"] == "worker-t1")
    assert worker["state"] == "READY" and worker["modelVersion"] == active.version
    assert worker["sha256Verified"] is True and worker["runsActiveVersion"] is True and worker["stale"] is False
    assert worker["loadedAt"] == loaded_at and worker["lastHeartbeatAt"]


async def test_wrong_digest_or_loading_is_not_verified(client, auth, service_headers, db):
    active = await _active_row(db)
    wrong = await client.post("/api/v1/internal/worker/status", headers=service_headers, json={
        "workerId": "worker-t2", "modelVersion": active.version, "sha256": "0" * 64, "state": "READY"})
    assert wrong.status_code == 200 and wrong.json()["sha256Verified"] is False
    loading = await client.post("/api/v1/internal/worker/status", headers=service_headers, json={
        "workerId": "worker-t2", "modelVersion": "1.2.0", "sha256": active.sha256, "state": "LOADING"})
    assert loading.status_code == 200 and loading.json()["sha256Verified"] is False
    failed = await client.post("/api/v1/internal/worker/status", headers=service_headers, json={
        "workerId": "worker-t2", "modelVersion": "1.2.0", "state": "FAILED", "detail": "sha256 mismatch"})
    assert failed.status_code == 200
    page = (await client.get(f"/api/v1/models/{MODEL}/deployment", headers=await auth.headers("iot"))).json()
    worker = next(w for w in page["workers"] if w["workerId"] == "worker-t2")
    assert worker["state"] == "FAILED" and worker["detail"] == "sha256 mismatch" and worker["sha256Verified"] is False


async def test_worker_report_validation(client, service_headers):
    bad_state = await client.post("/api/v1/internal/worker/status", headers=service_headers,
                                  json={"workerId": "worker-t3", "state": "RUNNING"})
    assert bad_state.status_code == 400 and bad_state.json()["error"]["code"] == "VALIDATION"
    bad_id = await client.post("/api/v1/internal/worker/status", headers=service_headers,
                               json={"workerId": "a b", "state": "READY"})
    assert bad_id.status_code == 400


async def test_deployment_roles_and_unknown_model(client, auth):
    for who in ("anh", "maria", "rider"):
        response = await client.get(f"/api/v1/models/{MODEL}/deployment", headers=await auth.headers(who))
        assert response.status_code == 403, who
    missing = await client.get("/api/v1/models/999/deployment", headers=await auth.headers("admin"))
    assert missing.status_code == 404


async def test_model_deployed_goes_to_the_staff_room_only(container):
    rooms = []
    hub = container.hub
    original = hub.broadcast

    async def record(room, message):
        rooms.append((room, message["type"]))

    hub.broadcast = record
    try:
        await hub.on_event("model.deployed", {"workerId": "worker-1", "state": "READY", "tenantId": 1})
    finally:
        hub.broadcast = original
    assert rooms == [("staff", "model.deployed")]
