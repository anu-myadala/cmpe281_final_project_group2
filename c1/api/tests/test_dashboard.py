"""Overview aggregate (cache, degraded mode, 502) and the vehicle map shape."""
from __future__ import annotations

from datetime import timedelta

from app.models import utcnow


async def test_overview_shape_and_cache(client, auth, container):
    headers = await auth.headers("maria")
    first = await client.get("/api/v1/dashboard/overview", headers=headers)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["tenant"] == {"tenantId": 2, "name": "Bay Shuttle LLC", "plan": "FLEET"}
    assert body["kpis"]["vehicles"] == 5 and body["kpis"]["online"] == 3 and body["kpis"]["openAlerts"] == 1
    assert body["kpis"]["alerts24h"] >= 0 and body["kpis"]["events24h"] >= 0
    assert [v["vehicleId"] for v in body["vehicles"]] == ["AV-10", "AV-11", "AV-12", "AV-13", "AV-14"]
    av12 = next(v for v in body["vehicles"] if v["vehicleId"] == "AV-12")
    assert av12["openAlerts"] == 1 and av12["vin"] == "5YJ3E1EA7KF300012"
    assert body["recentAlerts"][0]["alertId"] == 20470 and body["degraded"] is False
    assert body["asOf"].endswith("Z")
    calls = container.vehicle_client.calls
    second = await client.get("/api/v1/dashboard/overview", headers=headers)
    assert second.json()["asOf"] == body["asOf"] and container.vehicle_client.calls == calls  # served from cache


async def test_overview_degrades_to_cache_when_c2_is_down(client, auth, container):
    headers = await auth.headers("anh")
    fresh = await client.get("/api/v1/dashboard/overview", headers=headers)
    assert fresh.status_code == 200 and fresh.json()["kpis"]["vehicles"] == 1
    container.overview_cache.expire(1)  # the cache is stale but the last copy is kept
    container.vehicle_client.fail = True
    degraded = await client.get("/api/v1/dashboard/overview", headers=headers)
    assert degraded.status_code == 200
    assert degraded.json()["degraded"] is True and degraded.json()["kpis"]["vehicles"] == 1


async def test_overview_is_502_without_cache(client, auth, container):
    container.alert_client.fail = True
    response = await client.get("/api/v1/dashboard/overview", headers=await auth.headers("anh"))
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "BAD_GATEWAY"


async def test_overview_local_fallback_when_enabled(client, auth, container):
    container.alert_client.fail = True
    container.vehicle_client.fail = True
    container.settings.overview_local_fallback = True
    try:
        response = await client.get("/api/v1/dashboard/overview", headers=await auth.headers("maria"))
    finally:
        container.settings.overview_local_fallback = False
    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True and body["kpis"]["vehicles"] == 5 and body["kpis"]["openAlerts"] >= 1


async def test_map_shape_with_and_without_samples(client, auth):
    response = await client.get("/api/v1/dashboard/map", headers=await auth.headers("maria"))
    assert response.status_code == 200, response.text
    body = response.json()
    cars = {v["vehicleId"]: v for v in body["vehicles"]}
    assert set(cars) == {"AV-10", "AV-11", "AV-12", "AV-13", "AV-14"}
    expected_keys = {"vehicleId", "label", "status", "connection", "lat", "lon", "headingDeg", "speedKph",
                     "driveMode", "ignition", "sampleAt"}
    assert all(set(v) == expected_keys for v in body["vehicles"])
    # AV-14 never sent telemetry: only the vehicle status is known.
    av14 = cars["AV-14"]
    assert av14["status"] == "REGISTERED" and av14["lat"] is None and av14["lon"] is None
    assert av14["sampleAt"] is None and av14["connection"] is None and av14["headingDeg"] is None
    # AV-13 stopped 14 minutes ago: OFFLINE with its last position.
    av13 = cars["AV-13"]
    assert av13["connection"] == "OFFLINE" and av13["lat"] is not None and av13["sampleAt"].endswith("Z")
    # Online cars have a position near SJSU and a heading when moving.
    av10 = cars["AV-10"]
    assert 37.30 < av10["lat"] < 37.36 and -121.92 < av10["lon"] < -121.85
    assert av10["connection"] in ("ONLINE", "OFFLINE") and av10["speedKph"] is not None
    moving = [v for v in body["vehicles"] if v["speedKph"] and v["speedKph"] > 0]
    assert all(v["headingDeg"] is not None for v in moving)
    # Alert pins from C3.
    assert body["alerts"] == [{"alertId": 20470, "vehicleId": "AV-12", "lat": 37.336, "lon": -121.88,
                               "severity": "HIGH", "type": "SECURITY", "createdAt": "2026-10-30T10:00:00Z"}]
    assert body["degraded"] is False and body["asOf"].endswith("Z")


async def test_map_degrades_when_c3_is_down(client, auth, container):
    headers = await auth.headers("anh")
    ok = await client.get("/api/v1/dashboard/map", headers=headers)
    assert ok.json()["alerts"][0]["alertId"] == 20482
    container.pins_cache.expire(1)
    container.alert_client.fail = True
    degraded = await client.get("/api/v1/dashboard/map", headers=headers)
    assert degraded.status_code == 200
    assert degraded.json()["degraded"] is True
    assert degraded.json()["alerts"][0]["alertId"] == 20482  # last cached pins
    assert degraded.json()["vehicles"][0]["vehicleId"] == "AV-01"


async def test_map_connection_is_online_for_a_fresh_sample(client, auth, container):
    # The seed writes AV-01 samples up to the moment it ran, so this sample is placed just after the
    # newest one: it is then the latest sample however soon after the seed the test runs.
    newest = await container.mongo["vehicle_telemetry"].find_one(
        {"meta.vehicleId": "AV-01"}, sort=[("ts", -1)])
    ts = max(utcnow() - timedelta(seconds=5), newest["ts"] + timedelta(seconds=1)) if newest else utcnow()
    await container.mongo["vehicle_telemetry"].insert_one({
        "ts": ts, "meta": {"tenantId": "tA", "vehicleId": "AV-01"}, "speedKph": 0,
        "driveMode": "MANUAL", "ignition": True, "location": {"type": "Point", "coordinates": [-121.8811, 37.3352]}})
    response = await client.get("/api/v1/dashboard/map", headers=await auth.headers("anh"))
    car = response.json()["vehicles"][0]
    assert car["connection"] == "ONLINE" and car["lat"] == 37.3352 and car["speedKph"] == 0
