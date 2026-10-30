"""BR-11 feedback labels: one per event and user, export only when labels exist."""
from __future__ import annotations

from bson import ObjectId

EVENT_20482 = "6729b1c4e1f0a2d3c4b5a697"


async def test_label_once_per_event_and_user(client, auth):
    anh = await auth.headers("anh")
    first = await client.post(f"/api/v1/events/{EVENT_20482}/feedback", headers=anh, json={"label": "CONFIRMED"})
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["eventRef"] == EVENT_20482 and body["label"] == "CONFIRMED" and body["modelVersion"] == "sound-classifier-1.1.0"
    duplicate = await client.post(f"/api/v1/events/{EVENT_20482}/feedback", headers=anh, json={"label": "FALSE_ALARM"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["details"]["rule"] == "BR-11"
    # Another user may label the same event.
    staff = await client.post(f"/api/v1/events/{EVENT_20482}/feedback", headers=await auth.headers("staff"),
                              json={"label": "WRONG_CLASS", "correctedClass": "glass_break"})
    assert staff.status_code == 201 and staff.json()["correctedClass"] == "glass_break"


async def test_wrong_class_needs_a_valid_corrected_class(client, auth, container):
    doc = await container.mongo["audio_events"].find_one({"tenantId": "tB", "decision.alert": True})
    ref = str(doc["_id"])
    maria = await auth.headers("maria")
    missing = await client.post(f"/api/v1/events/{ref}/feedback", headers=maria, json={"label": "WRONG_CLASS"})
    assert missing.status_code == 400 and missing.json()["error"]["details"]["field"] == "correctedClass"
    unknown = await client.post(f"/api/v1/events/{ref}/feedback", headers=maria,
                                json={"label": "WRONG_CLASS", "correctedClass": "thunder"})
    assert unknown.status_code == 400
    bad_label = await client.post(f"/api/v1/events/{ref}/feedback", headers=maria, json={"label": "MAYBE"})
    assert bad_label.status_code == 400 and bad_label.json()["error"]["code"] == "VALIDATION"


async def test_event_of_another_tenant_is_404(client, auth):
    maria = await auth.headers("maria")  # tenant B cannot label tenant A's event
    response = await client.post(f"/api/v1/events/{EVENT_20482}/feedback", headers=maria, json={"label": "CONFIRMED"})
    assert response.status_code == 404
    unknown = await client.post(f"/api/v1/events/{ObjectId()}/feedback", headers=maria, json={"label": "CONFIRMED"})
    assert unknown.status_code == 404
    malformed = await client.post("/api/v1/events/not-an-object-id/feedback", headers=maria, json={"label": "CONFIRMED"})
    assert malformed.status_code == 404


async def test_export_labels_csv_and_404_without_labels(client, auth):
    iot = await auth.headers("iot")
    versions = {v["version"]: v for v in (await client.get("/api/v1/models/1/versions", headers=iot)).json()}
    with_labels = await client.get(f"/api/v1/models/1/feedback/export?versionId={versions['1.1.0']['versionId']}", headers=iot)
    assert with_labels.status_code == 200
    assert with_labels.headers["content-type"].startswith("text/csv")
    lines = with_labels.text.strip().splitlines()
    assert lines[0] == "eventRef,versionId,predictedClass,confidence,label,correctedClass,userId,ts"
    assert len(lines) - 1 == versions["1.1.0"]["labelCount"]
    assert any(EVENT_20482 in line and "crash,0.91" in line for line in lines)
    without = await client.get(f"/api/v1/models/1/feedback/export?versionId={versions['1.2.0']['versionId']}", headers=iot)
    assert without.status_code == 404 and without.json()["error"]["details"]["rule"] == "BR-11"
    whole_model = await client.get("/api/v1/models/1/feedback/export", headers=iot)
    assert whole_model.status_code == 200
    assert (await client.get("/api/v1/models/1/feedback/export", headers=await auth.headers("anh"))).status_code == 403
