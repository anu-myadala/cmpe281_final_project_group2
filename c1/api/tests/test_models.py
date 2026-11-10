"""BR-10 activation decision table (Part I, Table 31): one test per column
R1..R6, rollback, and the two-step upload."""
from __future__ import annotations

import hashlib

from sqlalchemy import select

from app.models import AuditLog, ModelVersion

MODEL = 1


async def _register(client, headers, container, version, macro_f1, data=None):
    """Begin + upload (into the fake bucket) + complete a version."""
    data = data or f"artifact {version}".encode() * 100
    begun = await client.post(f"/api/v1/models/{MODEL}/versions", headers=headers, json={"version": version})
    assert begun.status_code == 201, begun.text
    key = begun.json()["upload"]["key"]
    await container.storage.put_bytes(key, data)
    completed = await client.post(f"/api/v1/models/{MODEL}/versions/{version}/complete", headers=headers,
                                  json={"sha256": hashlib.sha256(data).hexdigest(), "sizeBytes": len(data),
                                        "metrics": {"macroF1": macro_f1, "accuracy": macro_f1 + 0.03,
                                                    "perClass": {"crash": {"f1": macro_f1}}}})
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "REGISTERED"
    return begun.json()["versionId"]


async def _active(client, headers):
    versions = (await client.get(f"/api/v1/models/{MODEL}/versions", headers=headers)).json()
    return next(v["version"] for v in versions if v["status"] == "ACTIVE")


async def test_models_list_from_seed(client, auth):
    response = await client.get("/api/v1/models", headers=await auth.headers("iot"))
    assert response.status_code == 200
    model = response.json()[0]
    assert model["name"] == "sound-classifier" and model["activeVersion"] == "1.1.0" and model["versions"] >= 2
    versions = (await client.get(f"/api/v1/models/{MODEL}/versions", headers=await auth.headers("iot"))).json()
    by_version = {v["version"]: v for v in versions}
    assert by_version["1.1.0"]["status"] == "ACTIVE" and by_version["1.1.0"]["metrics"]["macroF1"] == 0.6821
    assert by_version["1.1.0"]["labelCount"] >= 10 and len(by_version["1.1.0"]["metrics"]["perClass"]) == 7
    assert by_version["1.2.0"]["status"] == "REGISTERED" and by_version["1.2.0"]["sha256"]
    assert (await client.get("/api/v1/models/999/versions", headers=await auth.headers("iot"))).status_code == 404


async def test_upload_flow_begin_complete_download(client, auth, container):
    staff = await auth.headers("staff")
    begun = await client.post(f"/api/v1/models/{MODEL}/versions", headers=staff,
                              json={"version": "2.0.0", "notes": "upload test"})
    assert begun.status_code == 201
    body = begun.json()
    assert body["status"] == "UPLOADING" and body["upload"]["key"] == "models/1/2.0.0/model.pt"
    assert body["upload"]["url"].startswith("https://")
    assert (await client.post(f"/api/v1/models/{MODEL}/versions", headers=staff, json={"version": "2.0.0"})).status_code == 409
    assert (await client.post(f"/api/v1/models/{MODEL}/versions", headers=staff, json={"version": "v2"})).status_code == 400
    data = b"model bytes 2.0.0" * 50
    await container.storage.put_bytes(body["upload"]["key"], data)
    # Wrong size is refused and the row becomes FAILED.
    wrong = await client.post(f"/api/v1/models/{MODEL}/versions/2.0.0/complete", headers=staff,
                              json={"sha256": hashlib.sha256(data).hexdigest(), "sizeBytes": 1,
                                    "metrics": {"macroF1": 0.8, "accuracy": 0.8}})
    assert wrong.status_code == 422 and wrong.json()["error"]["details"]["rule"] == "BR-10"
    versions = {v["version"]: v for v in (await client.get(f"/api/v1/models/{MODEL}/versions", headers=staff)).json()}
    assert versions["2.0.0"]["status"] == "FAILED" and "size mismatch" in versions["2.0.0"]["notes"]
    # A FAILED version cannot be completed again (409 not UPLOADING).
    again = await client.post(f"/api/v1/models/{MODEL}/versions/2.0.0/complete", headers=staff,
                              json={"sha256": hashlib.sha256(data).hexdigest(), "sizeBytes": len(data),
                                    "metrics": {"macroF1": 0.8, "accuracy": 0.8}})
    assert again.status_code == 409
    # Checksum mismatch on another version.
    begun2 = await client.post(f"/api/v1/models/{MODEL}/versions", headers=staff, json={"version": "2.0.1"})
    await container.storage.put_bytes(begun2.json()["upload"]["key"], data)
    bad_sha = await client.post(f"/api/v1/models/{MODEL}/versions/2.0.1/complete", headers=staff,
                                json={"sha256": "0" * 64, "sizeBytes": len(data), "metrics": {"macroF1": 0.8, "accuracy": 0.8}})
    assert bad_sha.status_code == 422 and "sha256" in bad_sha.json()["error"]["message"]
    # Happy path and download.
    await _register(client, staff, container, "2.0.2", 0.85, data)
    download = await client.get(f"/api/v1/models/{MODEL}/versions/2.0.2/download", headers=staff, follow_redirects=False)
    assert download.status_code == 302 and "models/1/2.0.2/model.pt" in download.headers["location"]
    assert (await client.get(f"/api/v1/models/{MODEL}/versions/2.0.0/download", headers=staff,
                             follow_redirects=False)).status_code == 409


async def test_r1_target_status_must_be_registered_or_inactive(client, auth):
    staff = await auth.headers("staff")
    active = await client.post(f"/api/v1/models/{MODEL}/versions/1.1.0/activate", headers=staff, json={})
    assert active.status_code == 409  # already ACTIVE
    begun = await client.post(f"/api/v1/models/{MODEL}/versions", headers=staff, json={"version": "3.0.0"})
    assert begun.status_code == 201
    uploading = await client.post(f"/api/v1/models/{MODEL}/versions/3.0.0/activate", headers=staff, json={})
    assert uploading.status_code == 409
    assert (await client.post(f"/api/v1/models/{MODEL}/versions/9.9.9/activate", headers=staff, json={})).status_code == 404


async def test_r2_artifact_missing_or_changed_is_refused(client, auth, container):
    staff = await auth.headers("staff")
    await _register(client, staff, container, "3.1.0", 0.90)
    del container.storage.objects["models/1/3.1.0/model.pt"]
    response = await client.post(f"/api/v1/models/{MODEL}/versions/3.1.0/activate", headers=staff, json={})
    assert response.status_code == 422
    assert response.json()["error"]["details"] == {"rule": "BR-10", "reason": "artifact"}
    await container.storage.put_bytes("models/1/3.1.0/model.pt", b"tampered")
    changed = await client.post(f"/api/v1/models/{MODEL}/versions/3.1.0/activate", headers=staff, json={})
    assert changed.status_code == 422 and changed.json()["error"]["details"]["reason"] == "artifact"


async def test_r3_metrics_missing_is_refused(client, auth, container, db):
    staff = await auth.headers("staff")
    version_id = await _register(client, staff, container, "3.2.0", 0.90)
    row = await db.get(ModelVersion, version_id)
    row.metrics_json = None
    await db.commit()
    response = await client.post(f"/api/v1/models/{MODEL}/versions/3.2.0/activate", headers=staff, json={})
    assert response.status_code == 422 and response.json()["error"]["details"]["reason"] == "metrics"


async def test_r4_macro_f1_drop_over_margin_is_refused(client, auth, container):
    staff = await auth.headers("staff")
    await _register(client, staff, container, "3.3.0", 0.62)
    response = await client.post(f"/api/v1/models/{MODEL}/versions/3.3.0/activate", headers=staff, json={})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["message"] == "Macro-F1 is more than 0.05 below the active version"
    assert error["details"] == {"rule": "BR-10", "activeMacroF1": 0.6821, "targetMacroF1": 0.62}
    # force without a note is not an override
    forced_without_note = await client.post(f"/api/v1/models/{MODEL}/versions/3.3.0/activate", headers=staff,
                                            json={"force": True})
    assert forced_without_note.status_code == 422


async def test_r5_force_with_note_overrides_the_gate(client, auth, container, db):
    staff = await auth.headers("staff")
    assert await _active(client, staff) == "1.1.0"
    response = await client.post(f"/api/v1/models/{MODEL}/versions/3.3.0/activate", headers=staff,
                                 json={"force": True, "note": "fixes the siren class"})
    assert response.status_code == 200
    assert response.json() == {"active": "3.3.0", "previous": "1.1.0", "modelId": MODEL}
    assert await _active(client, staff) == "3.3.0"
    audit = (await db.execute(select(AuditLog).where(AuditLog.action == "MODEL_ACTIVATE"))).scalars().all()
    assert audit[-1].after_json == {"active": "3.3.0", "force": True, "note": "fixes the siren class"}
    assert audit[-1].before_json == {"active": "1.1.0"}
    # Roll back to 1.1.0 so that the later tests start from the seed state.
    rolled = await client.post(f"/api/v1/models/{MODEL}/rollback", headers=staff, json={"note": "undo test"})
    assert rolled.status_code == 200 and rolled.json() == {"active": "1.1.0", "previous": "3.3.0", "modelId": MODEL}


async def test_r6_activation_passes_and_publishes(client, auth, container):
    staff = await auth.headers("staff")
    received = []
    container.bus.subscribe("model.activated", lambda topic, payload: received.append(payload))
    before_mqtt = len(container.mqtt.published)
    response = await client.post(f"/api/v1/models/{MODEL}/versions/1.2.0/activate", headers=staff, json={})
    assert response.status_code == 200, response.text
    assert response.json()["active"] == "1.2.0" and response.json()["previous"] == "1.1.0"
    assert received == [{"modelId": MODEL, "version": "1.2.0"}]
    mqtt = container.mqtt.published[before_mqtt:]
    assert mqtt and mqtt[-1]["topic"] == "scicp/v1/platform/model/activated"
    assert mqtt[-1]["payload"] == {"modelId": MODEL, "version": "1.2.0"} and mqtt[-1]["retain"] is True and mqtt[-1]["qos"] == 1
    versions = {v["version"]: v for v in (await client.get(f"/api/v1/models/{MODEL}/versions", headers=staff)).json()}
    assert versions["1.1.0"]["status"] == "INACTIVE" and versions["1.1.0"]["deactivatedAt"]
    assert versions["1.2.0"]["status"] == "ACTIVE" and versions["1.2.0"]["activatedAt"]
    assert sum(1 for v in versions.values() if v["status"] == "ACTIVE") == 1
    models = (await client.get("/api/v1/models", headers=staff)).json()
    assert models[0]["activeVersion"] == "1.2.0"


async def test_rollback_reactivates_previous_version(client, auth, container, db):
    staff = await auth.headers("staff")
    assert await _active(client, staff) == "1.2.0"
    response = await client.post(f"/api/v1/models/{MODEL}/rollback", headers=staff, json={"note": "regression on crash"})
    assert response.status_code == 200
    assert response.json() == {"active": "1.1.0", "previous": "1.2.0", "modelId": MODEL}
    assert await _active(client, staff) == "1.1.0"
    actions = [r[0] for r in await db.execute(select(AuditLog.action).where(AuditLog.entity == "model_version"))]
    assert "MODEL_ROLLBACK" in actions
    # A model with nothing to roll back to answers 409.
    created = await client.post("/api/v1/models", headers=staff, json={"name": "fresh-model", "task": "test"})
    assert created.status_code == 201
    nothing = await client.post(f"/api/v1/models/{created.json()['modelId']}/rollback", headers=staff, json={"note": "x"})
    assert nothing.status_code == 409
    assert (await client.post("/api/v1/models", headers=staff, json={"name": "fresh-model", "task": "t"})).status_code == 409
