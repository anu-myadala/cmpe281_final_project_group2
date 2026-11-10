"""C2 tests: the stub contract C1 relies on, tenancy, and one test per column of the decision
tables of Section 4 of the C2 design (marked with the case id, e.g. V4, U3, C5)."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from c2_service.models import Clip, Device, DeviceCommand, Vehicle
from c2_service.services.commands import check_config_values
from c2_service.services.status import decide_status
from c2_service.timeutil import utcnow
from c2_service.errors import BusinessRule, Validation

from conftest import C1_CALLS, auth, make_wav

STUB_VEHICLE_KEYS = {"vehicleId", "vin", "label", "make", "model", "year", "status", "connection", "lastSeenAt",
                     "retentionDays", "deviceSummary", "tenantId", "iotThingName", "position", "provisionedAt",
                     "createdAt", "updatedAt"}
STUB_DEVICE_KEYS = {"deviceId", "type", "status", "lastSeenAt", "vehicleId", "muted", "firmware"}


# -- the contract with C1 (stubs/c2.py) -------------------------------------------------------------------
async def test_vehicle_list_matches_the_stub(client, api):
    r = await client.get(f"{api}/vehicles?tenantId=2&size=100", headers=auth("service"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"items", "page", "size", "total"}
    by_code = {v["vehicleId"]: v for v in body["items"]}
    assert set(by_code) == {"AV-10", "AV-11", "AV-12", "AV-13", "AV-14"}
    assert STUB_VEHICLE_KEYS <= set(by_code["AV-10"])
    assert by_code["AV-10"]["connection"] == "ONLINE"
    assert by_code["AV-13"]["connection"] == "OFFLINE"          # telemetry stopped 14 min ago in the seed
    assert by_code["AV-14"]["connection"] == "NEVER"
    assert by_code["AV-10"]["deviceSummary"] == {"total": 3, "online": 3, "offline": 0, "fault": 0, "disabled": 0}


async def test_devices_and_telemetry_match_the_stub(client, api):
    r = await client.get(f"{api}/vehicles/AV-01/devices?tenantId=1", headers=auth("iot"))
    assert r.status_code == 200
    devices = r.json()
    assert [d["deviceId"] for d in devices] == ["dev-0110", "dev-0111", "dev-0112"]
    assert STUB_DEVICE_KEYS <= set(devices[0])
    r = await client.get(f"{api}/vehicles/AV-01/telemetry?limit=5", headers=auth("anh"))
    assert r.status_code == 200 and len(r.json()) == 5
    assert set(r.json()[0]) >= {"ts", "speedKph", "driveMode", "ignition", "location", "network"}
    r = await client.get(f"{api}/devices?tenantId=2", headers=auth("iot"))
    assert r.json()["total"] == 15


async def test_tenancy(client, api):
    assert (await client.get(f"{api}/vehicles/AV-10", headers=auth("anh"))).status_code == 404
    assert (await client.get(f"{api}/vehicles", headers=auth("staff"))).status_code == 400
    assert (await client.get(f"{api}/vehicles")).status_code == 401
    r = await client.get(f"{api}/vehicles", headers=auth("rider"))
    assert [v["vehicleId"] for v in r.json()["items"]] == ["AV-01"]


# -- 4.1 vehicles ----------------------------------------------------------------------------------------
async def test_vehicle_decision_table(client, api):
    body = {"vehicleId": "AV-02", "vin": "7SAYGDEE5PA000202", "make": "Tesla", "model": "Model Y", "year": 2023,
            "label": "Anh's second car"}
    assert (await client.post(f"{api}/vehicles", json=body, headers=auth("anh"))).status_code == 403      # V1
    bad = dict(body, vin="7SAYGDEE5PA00020O")
    r = await client.post(f"{api}/vehicles?tenantId=1", json=bad, headers=auth("iot"))
    assert r.status_code == 422 and r.json()["error"]["details"]["rule"] == "BR-C2-03"                     # V2
    r = await client.post(f"{api}/vehicles?tenantId=1", json=body, headers=auth("iot"))
    assert r.status_code == 201, r.text                                                                     # V5
    assert r.json()["status"] == "REGISTERED" and r.json()["connection"] == "NEVER"
    assert any("subscriptions/defaults" in url for _, url, _ in C1_CALLS)
    assert (await client.post(f"{api}/vehicles?tenantId=1", json=body, headers=auth("iot"))).status_code == 409  # V3
    third = dict(body, vehicleId="AV-03", vin="7SAYGDEE5PA000203")
    assert (await client.post(f"{api}/vehicles?tenantId=1", json=third, headers=auth("iot"))).status_code == 201
    fourth = dict(body, vehicleId="AV-04", vin="7SAYGDEE5PA000204")
    r = await client.post(f"{api}/vehicles?tenantId=1", json=fourth, headers=auth("iot"))
    assert r.status_code == 422 and r.json()["error"]["details"]["rule"] == "BR-C2-02"                     # V4
    r = await client.put(f"{api}/vehicles/AV-02?tenantId=1", json={"vin": "X"}, headers=auth("iot"))
    assert r.status_code == 422


async def test_retire(client, api, services):
    r = await client.delete(f"{api}/vehicles/AV-03?tenantId=1", headers=auth("iot"))
    assert r.status_code == 400                                                                             # R1
    r = await client.delete(f"{api}/vehicles/AV-03?tenantId=1&confirm=true", headers=auth("iot"))
    assert r.status_code == 200 and r.json()["status"] == "RETIRED"                                        # R3
    assert (await client.delete(f"{api}/vehicles/AV-03?tenantId=1&confirm=true", headers=auth("iot"))).status_code == 409


# -- 4.2 devices -----------------------------------------------------------------------------------------
async def test_device_decision_table(client, api):
    mic = {"type": "MIC_EXTERIOR", "position": "front bumper", "serialNo": "MIC-AV02-EXT"}
    r = await client.post(f"{api}/vehicles/AV-02/devices?tenantId=1", json=mic, headers=auth("iot"))
    assert r.status_code == 201 and r.json()["deviceId"] == "dev-0212" and r.json()["status"] == "PROVISIONING"  # D3
    r = await client.post(f"{api}/vehicles/AV-02/devices?tenantId=1", json=dict(mic, type="MIC_INTERIOR"),
                          headers=auth("iot"))
    assert r.status_code == 409                                                                             # D2 serial
    r = await client.post(f"{api}/vehicles/AV-02/devices?tenantId=1",
                          json={"type": "MIC_INTERIOR", "serialNo": "MIC-AV02-INT"}, headers=auth("iot"))
    assert r.json()["deviceId"] == "dev-0211"
    r = await client.post(f"{api}/vehicles/AV-03/devices?tenantId=1", json={"type": "CAMERA", "serialNo": "CAM-3"},
                          headers=auth("iot"))
    assert r.json()["error"]["details"]["rule"] == "BR-C2-05"                                              # D1
    r = await client.delete(f"{api}/devices/dev-0110", headers=auth("iot"))
    assert r.status_code == 422 and r.json()["error"]["details"]["rule"] == "BR-C2-06"                     # D6


# -- 4.3 provisioning ------------------------------------------------------------------------------------
async def test_provisioning(client, api):
    r = await client.post(f"{api}/vehicles/AV-03/provision?tenantId=1", json={}, headers=auth("iot"))
    assert r.json()["error"]["details"]["rule"] == "BR-C2-07"                                              # P1
    r = await client.post(f"{api}/vehicles/AV-02/provision?tenantId=1", json={}, headers=auth("iot"))
    assert r.status_code == 201, r.text                                                                     # P2
    body = r.json()
    assert body["status"] == "PROVISIONED" and body["mqtt"]["username"] == "tA-AV-02"
    assert body["devices"] == ["dev-0210", "dev-0211", "dev-0212"]
    assert "ingestToken: " in body["agentYaml"]
    assert (await client.post(f"{api}/vehicles/AV-02/provision?tenantId=1", json={}, headers=auth("iot"))).status_code == 409  # P3
    r2 = await client.post(f"{api}/vehicles/AV-02/provision?tenantId=1", json={"rotate": True}, headers=auth("iot"))
    assert r2.status_code == 201 and r2.json()["credentialVersion"] == body["credentialVersion"] + 1         # P4
    pytest.av02_old_token = body["api"]["ingestToken"]
    pytest.av02_token = r2.json()["api"]["ingestToken"]


# -- 4.6 upload and 2.7 the internal fetch -----------------------------------------------------------------
def _upload(token, device="dev-0212", event_id=None, data=None):
    return dict(files={"file": ("clip.wav", data or make_wav(), "audio/wav")},
                data={"deviceId": device, "eventId": event_id or str(uuid.uuid4())},
                headers={"Authorization": f"Bearer {token}"})


async def test_upload_decision_table(client, api):
    token = pytest.av02_token
    assert (await client.post(f"{api}/ingest/clips", **_upload(pytest.av02_old_token))).status_code == 401  # U1
    r = await client.post(f"{api}/ingest/clips", **_upload(token, device="dev-0210"))
    assert r.status_code == 422 and r.json()["error"]["details"]["rule"] == "BR-C2-15"                     # U2
    event_id = str(uuid.uuid4())
    r = await client.post(f"{api}/ingest/clips", **_upload(token, event_id=event_id))
    assert r.status_code == 201, r.text                                                                     # U6
    clip = r.json()
    assert clip["s3Key"].startswith("tenants/tA/vehicles/AV-02/") and clip["s3Key"].endswith(f"{clip['clipId']}.wav")
    assert clip["durationMs"] == 5000 and clip["sampleRate"] == 16000
    r = await client.post(f"{api}/ingest/clips", **_upload(token, event_id=event_id))
    assert r.status_code == 200 and r.json()["clipId"] == clip["clipId"]                                   # U3
    r = await client.post(f"{api}/ingest/clips", **_upload(token, data=make_wav(channels=2)))
    assert r.status_code == 415                                                                             # U5
    r = await client.post(f"{api}/ingest/clips", **_upload(token, data=make_wav(seconds=2)))
    assert r.status_code == 400
    pytest.clip_id = clip["clipId"]


async def test_internal_clip_fetch(client, api):
    r = await client.get(f"{api}/internal/clips/{pytest.clip_id}", headers=auth("service"))
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert r.content[:4] == b"RIFF" and r.headers["x-vehicle-id"] == "AV-02"
    assert (await client.get(f"{api}/internal/clips/{pytest.clip_id}", headers=auth("iot"))).status_code == 403
    assert (await client.get(f"{api}/internal/clips/{pytest.clip_id}")).status_code == 401
    assert (await client.get(f"{api}/internal/clips/ffffffff", headers=auth("service"))).status_code == 404


async def test_clip_list_play_delete(client, api):
    r = await client.get(f"{api}/vehicles/AV-02/clips", headers=auth("anh"))
    assert r.status_code == 200 and r.json()["total"] == 1
    assert r.json()["items"][0]["topClass"] is None                     # not classified yet
    assert (await client.get(f"{api}/vehicles/AV-02/clips", headers=auth("rider"))).status_code == 403
    r = await client.get(f"{api}/clips/{pytest.clip_id}/url", headers=auth("anh"))
    url = r.json()["url"].replace("/c2/api/v1", api)
    assert (await client.get(url)).content[:4] == b"RIFF"                                                   # K2
    assert (await client.get(url.replace("sig=", "sig=0"))).status_code == 401
    assert (await client.delete(f"{api}/clips/{pytest.clip_id}", headers=auth("staff"))).status_code == 403  # K3
    assert (await client.delete(f"{api}/clips/{pytest.clip_id}", headers=auth("anh"))).status_code == 204
    assert (await client.delete(f"{api}/clips/{pytest.clip_id}", headers=auth("anh"))).status_code == 410   # K4
    assert (await client.get(f"{api}/internal/clips/{pytest.clip_id}", headers=auth("service"))).status_code == 410
    r = await client.get(f"{api}/vehicles/AV-02/clips", headers=auth("anh"))
    assert r.json()["items"][0]["state"] == "DELETED"


async def test_retention(client, api, app, services):
    r = await client.put(f"{api}/vehicles/AV-02/retention", json={"retentionDays": 3}, headers=auth("anh"))
    assert r.status_code == 422
    r = await client.put(f"{api}/vehicles/AV-02/retention", json={"retentionDays": 30}, headers=auth("anh"))
    assert r.status_code == 200 and r.json()["retentionDays"] == 30
    r = await client.post(f"{api}/ingest/clips", **_upload(pytest.av02_token))
    clip_id = r.json()["clipId"]
    async with app.state.session_factory() as session:
        clip = await session.get(Clip, clip_id)
        assert abs((clip.expire_at - clip.captured_at) - timedelta(days=30)) < timedelta(seconds=1)
        clip.expire_at = utcnow() - timedelta(minutes=1)
        await session.commit()
    assert await services.clips.run_retention() == 1                                                        # K6
    assert (await client.get(f"{api}/internal/clips/{clip_id}", headers=auth("service"))).status_code == 410


# -- 4.5 status ------------------------------------------------------------------------------------------
async def test_status_from_telemetry_sweep_and_last_will(client, api, services, events):
    events.events.clear()
    await services.telemetry.on_telemetry("tA", "AV-02", {"speedKph": 0, "devices": [
        {"deviceId": "dev-0211", "ok": True}, {"deviceId": "dev-0212", "ok": True}]})
    r = await client.get(f"{api}/vehicles/AV-02?tenantId=1", headers=auth("iot"))
    assert r.json()["status"] == "ACTIVE" and r.json()["connection"] == "ONLINE"                            # BR-C2-21
    assert {e[1]["deviceId"] for e in events.events if e[0] == "device.status"} == {"dev-0210", "dev-0211", "dev-0212"}
    await services.tracker.on_status("tA", "AV-02", {"type": "device", "deviceId": "dev-0211", "ok": False,
                                                     "reason": "NO_SIGNAL"})
    devices = {d["deviceId"]: d for d in (await client.get(f"{api}/vehicles/AV-02/devices?tenantId=1",
                                                             headers=auth("iot"))).json()}
    assert devices["dev-0211"]["status"] == "FAULT" and devices["dev-0211"]["faultReason"] == "NO_SIGNAL"   # S4
    assert await services.tracker.sweep(utcnow() + timedelta(seconds=45)) >= 3                            # S3
    r = await client.get(f"{api}/vehicles/AV-02?tenantId=1", headers=auth("iot"))
    assert r.json()["deviceSummary"]["offline"] == 3
    for tenant, car in (("tA", "AV-01"), ("tB", "AV-10"), ("tB", "AV-11"), ("tB", "AV-12")):   # the sweep hit them too
        n = int(car[-2:])
        await services.telemetry.on_telemetry(tenant, car, {"devices": [{"deviceId": f"dev-{n:02d}11", "ok": True},
                                                                       {"deviceId": f"dev-{n:02d}12", "ok": True}]})
    await services.telemetry.on_telemetry("tA", "AV-02", {"devices": [{"deviceId": "dev-0211", "ok": True},
                                                                      {"deviceId": "dev-0212", "ok": True}]})
    await services.tracker.on_status("tA", "AV-02", {"type": "agent", "state": "offline"})                 # last will
    r = await client.get(f"{api}/vehicles/AV-02?tenantId=1", headers=auth("iot"))
    assert r.json()["connection"] == "OFFLINE" and r.json()["deviceSummary"]["offline"] == 3
    await services.tracker.on_status("tA", "AV-02", {"type": "agent", "state": "online"})
    await services.telemetry.on_telemetry("tA", "AV-02", {"devices": [{"deviceId": "dev-0211", "ok": True},
                                                                      {"deviceId": "dev-0212", "ok": True}]})
    r = await client.get(f"{api}/vehicles/AV-02?tenantId=1", headers=auth("iot"))
    assert r.json()["deviceSummary"]["online"] == 3


def test_decide_status_table():
    now = utcnow()
    d = Device(status="ONLINE", last_seen_at=now, reported_ok=True)
    assert decide_status(Device(status="DISABLED", last_seen_at=now), now, 30, False) == "DISABLED"   # S1
    assert decide_status(Device(status="PROVISIONING", last_seen_at=None), now, 30, False) == "PROVISIONING"  # S2
    assert decide_status(d, now, 30, True) == "OFFLINE"                                                # S3
    assert decide_status(Device(status="ONLINE", last_seen_at=now - timedelta(seconds=31)), now, 30, False) == "OFFLINE"
    assert decide_status(Device(status="ONLINE", last_seen_at=now, reported_ok=False), now, 30, False) == "FAULT"  # S4
    assert decide_status(d, now, 30, False) == "ONLINE"                                                # S5/S6


# -- 4.4 settings and commands ---------------------------------------------------------------------------
async def test_config_change_confirmed_by_the_car(client, api, services, gateway, events):
    r = await client.put(f"{api}/devices/dev-0112/config", json={"micGainDb": 40}, headers=auth("iot"))
    assert r.status_code == 422 and r.json()["error"]["details"]["rule"] == "BR-C2-12"                     # C2
    r = await client.put(f"{api}/devices/dev-0110/config", json={"micGainDb": 12}, headers=auth("iot"))
    assert r.json()["error"]["details"]["rule"] == "BR-C2-11"                                              # C1
    r = await client.put(f"{api}/devices/dev-0112/config", json={"micGainDb": 12}, headers=auth("iot"))
    assert r.status_code == 202 and r.json()["state"] == "SENT" and r.json()["configVersion"] == 2         # C4
    msg = gateway.last("config")
    assert msg["type"] == "SET_CONFIG" and msg["settings"] == {"micGainDb": 12, "detectionThreshold": 0.4}
    events.events.clear()
    await services.tracker.on_status("tA", "AV-01", {"type": "ack", "commandId": msg["commandId"], "result": "APPLIED",
                                                     "reported": {"micGainDb": 12, "detectionThreshold": 0.4}})
    dev = (await client.get(f"{api}/devices/dev-0112", headers=auth("iot"))).json()
    assert dev["config"]["state"] == "CONFIRMED" and dev["config"]["values"]["micGainDb"] == 12
    assert events.events[-1][1]["config"]["state"] == "CONFIRMED"                                          # A2
    assert await services.commands.on_ack("tA", "AV-01", {"commandId": msg["commandId"], "result": "APPLIED"}) is None  # A1


async def test_instant_ack_is_not_lost(client, api, services, gateway):
    """A car on the same host acks within a millisecond, while C2 is still inside publish().
    The ack must find the command already SENT (it was dropped as unknown before the fix)."""
    original = gateway.publish
    results = []

    async def publish_and_ack(topic, payload, qos=1):
        ok = await original(topic, payload, qos)
        if payload.get("type") == "SET_CONFIG":
            results.append(await services.commands.on_ack("tB", "AV-12", {
                "commandId": payload["commandId"], "result": "APPLIED", "reported": payload["settings"]}))
        return ok

    gateway.publish = publish_and_ack
    try:
        for gain in (6, 9):
            r = await client.put(f"{api}/devices/dev-1212/config", json={"micGainDb": gain}, headers=auth("iot"))
            assert r.status_code == 202
            cmd = (await client.get(f"{api}/commands/{r.json()['commandId']}", headers=auth("iot"))).json()
            assert cmd["status"] == "APPLIED"
        assert results == ["APPLIED", "APPLIED"]
        dev = (await client.get(f"{api}/devices/dev-1212", headers=auth("iot"))).json()
        assert dev["config"]["state"] == "CONFIRMED" and dev["config"]["values"]["micGainDb"] == 9
    finally:
        gateway.publish = original


async def test_config_pending_while_offline_then_resent(client, api, services, gateway):
    r = await client.put(f"{api}/devices/dev-1312/config", json={"detectionThreshold": 0.5}, headers=auth("iot"))
    assert r.json()["state"] == "PENDING"                                                                   # C5
    first = r.json()["commandId"]
    r = await client.put(f"{api}/devices/dev-1312/config", json={"micGainDb": 9}, headers=auth("iot"))
    second = r.json()["commandId"]
    async with services.commands.session_factory() as session:
        assert (await session.get(DeviceCommand, first)).status == "SUPERSEDED"                            # C3
    await services.telemetry.on_telemetry("tB", "AV-13", {"devices": [{"deviceId": "dev-1311", "ok": True},
                                                                      {"deviceId": "dev-1312", "ok": True}]})
    assert gateway.last("config")["commandId"] == second
    async with services.commands.session_factory() as session:
        cmd = await session.get(DeviceCommand, second)
        assert cmd.status == "SENT"
        cmd.sent_at = utcnow() - timedelta(seconds=40)
        await session.commit()
    assert await services.commands.timeouts() == 1                                                          # C6
    async with services.commands.session_factory() as session:
        assert (await session.get(DeviceCommand, second)).status == "TIMED_OUT"


async def test_mute_and_disable(client, api, services, gateway):
    r = await client.post(f"{api}/devices/dev-1011/commands", json={"command": "MUTE"}, headers=auth("iot"))
    assert r.status_code == 202 and r.json()["state"] == "SENT"
    cmd = gateway.last("command")
    await services.commands.on_ack("tB", "AV-10", {"commandId": cmd["commandId"], "result": "APPLIED"})
    assert (await client.get(f"{api}/devices/dev-1011", headers=auth("iot"))).json()["muted"] is True
    r = await client.post(f"{api}/devices/dev-1010/commands", json={"command": "MUTE"}, headers=auth("iot"))
    assert r.json()["error"]["details"]["rule"] == "BR-C2-11"
    r = await client.post(f"{api}/devices/dev-1012/commands", json={"command": "FIRMWARE_UPDATE"}, headers=auth("iot"))
    assert r.status_code == 501
    r = await client.put(f"{api}/devices/dev-1012", json={"enabled": False}, headers=auth("iot"))
    assert r.json()["status"] == "DISABLED"                                                                # D4
    await services.telemetry.on_telemetry("tB", "AV-10", {"devices": [{"deviceId": "dev-1012", "ok": True}]})
    assert (await client.get(f"{api}/devices/dev-1012", headers=auth("iot"))).json()["status"] == "DISABLED"  # BR-C2-10
    r = await client.put(f"{api}/devices/dev-1012", json={"enabled": True}, headers=auth("iot"))
    assert r.json()["status"] == "PROVISIONING"                                                            # D5


def test_config_ranges():
    assert check_config_values({"micGainDb": 24, "detectionThreshold": 0.95}) == {"micGainDb": 24, "detectionThreshold": 0.95}
    for bad in ({"micGainDb": 25}, {"micGainDb": 1.5}, {"detectionThreshold": 0.05}, {"detectionThreshold": 0.555},
                {"sampleRateHz": 44100, "micGainDb": 1}):
        with pytest.raises(BusinessRule):
            check_config_values(bad)
    with pytest.raises(Validation):
        check_config_values({})


# -- 4.8 network -----------------------------------------------------------------------------------------
async def test_network(client, api, gateway, app):
    bad = {"profiles": [{"priority": 1, "ssid": "Depot", "securityType": "WPA2_PSK", "password": "short"}]}
    r = await client.put(f"{api}/vehicles/AV-12/network?tenantId=2", json=bad, headers=auth("iot"))
    assert r.status_code == 422 and r.json()["error"]["details"]["rule"] == "BR-C2-25"                     # N4
    dup = {"profiles": [{"priority": 1, "ssid": "A", "securityType": "OPEN"}, {"priority": 1, "ssid": "B", "securityType": "OPEN"}]}
    assert (await client.put(f"{api}/vehicles/AV-12/network?tenantId=2", json=dup, headers=auth("iot"))).status_code == 422  # N1
    good = {"profiles": [{"priority": 1, "ssid": "BayShuttle-Depot", "securityType": "WPA2_PSK", "password": "depot-pass-2026"},
                         {"priority": 2, "ssid": "SJSU-Guest", "securityType": "OPEN"}]}
    r = await client.put(f"{api}/vehicles/AV-12/network?tenantId=2", json=good, headers=auth("iot"))
    assert r.status_code == 202 and r.json()["state"] == "SENT"                                            # N5
    msg = gateway.last("config")
    assert msg["type"] == "SET_NETWORK" and msg["profiles"][0]["password"] == "depot-pass-2026"
    r = await client.get(f"{api}/vehicles/AV-12/network?tenantId=2", headers=auth("iot"))
    assert r.json()["profiles"][0] == {"priority": 1, "ssid": "BayShuttle-Depot", "securityType": "WPA2_PSK", "passwordSet": True}
    assert "depot-pass-2026" not in r.text
    keep = {"profiles": [{"priority": 1, "ssid": "BayShuttle-Depot", "securityType": "WPA2_PSK"}]}
    assert (await client.put(f"{api}/vehicles/AV-12/network?tenantId=2", json=keep, headers=auth("iot"))).status_code == 202
    assert (await client.put(f"{api}/vehicles/AV-12/network?tenantId=2", json=good, headers=auth("maria"))).status_code == 403


async def test_health(client, api):
    r = await client.get(f"{api}/health")
    assert r.status_code == 200 and r.json()["database"] == "ok"
