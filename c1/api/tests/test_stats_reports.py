"""BR-08 / BR-09 range decision table (Part I, Table 30): one test per column
R1..R4, plus the four report types in JSON and CSV."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models import utcnow
from app.services import report_service


def _range(days: float):
    end = utcnow()
    start = end - timedelta(days=days)
    return {"from": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "to": end.strftime("%Y-%m-%dT%H:%M:%SZ")}


async def test_r1_two_days_bucketed_by_hour(client, auth):
    response = await client.get("/api/v1/dashboard/stats", params=_range(2), headers=await auth.headers("maria"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["granularity"] == "hour"
    assert set(body["deviceStatus"]) >= {"ONLINE", "OFFLINE", "FAULT", "PROVISIONING", "DISABLED"}
    assert body["deviceStatus"]["ONLINE"] == 6 and body["deviceStatus"]["OFFLINE"] == 2
    assert 0 <= body["falseAlarmRate"] <= 1


async def test_r2_ten_days_bucketed_by_day(client, auth):
    response = await client.get("/api/v1/dashboard/stats", params=_range(10), headers=await auth.headers("staff") |
                                {}, )
    assert response.status_code == 400  # staff without tenantId
    response = await client.get("/api/v1/dashboard/stats", params={**_range(10), "tenantId": 2},
                                headers=await auth.headers("staff"))
    assert response.status_code == 200
    body = response.json()
    assert body["granularity"] == "day"
    assert body["alertsByTypePerPeriod"] and all("EMERGENCY" in p for p in body["alertsByTypePerPeriod"])
    assert body["eventsByClass"] and body["eventsByClass"][0]["count"] >= body["eventsByClass"][-1]["count"]
    assert body["meanAckSeconds"] is None or body["meanAckSeconds"] > 0


async def test_r3_csv_over_row_cap_is_br09(client, auth, monkeypatch):
    monkeypatch.setattr(report_service, "ROW_CAP", 0)
    response = await client.get("/api/v1/reports/false-alarms", params={**_range(14), "format": "csv"},
                                headers=await auth.headers("maria"))
    assert response.status_code == 422
    assert response.json()["error"]["details"]["rule"] == "BR-09"


async def test_r4_over_92_days_is_br08(client, auth):
    for path in ("/api/v1/dashboard/stats", "/api/v1/reports/alerts-by-day"):
        response = await client.get(path, params=_range(93), headers=await auth.headers("maria"))
        assert response.status_code == 422, path
        assert response.json()["error"]["details"]["rule"] == "BR-08"
    ok = await client.get("/api/v1/dashboard/stats", params=_range(92), headers=await auth.headers("maria"))
    assert ok.status_code == 200 and ok.json()["granularity"] == "day"


async def test_stats_roles(client, auth):
    for who in ("anh", "rider", "iot"):
        response = await client.get("/api/v1/dashboard/stats", params=_range(1), headers=await auth.headers(who))
        assert response.status_code == 403, who
    assert (await client.get("/api/v1/dashboard/stats", params={**_range(1), "tenantId": 1},
                             headers=await auth.headers("admin"))).status_code == 200


async def test_invalid_range_values(client, auth):
    bad = await client.get("/api/v1/dashboard/stats", params={"from": "yesterday", "to": "now"},
                           headers=await auth.headers("maria"))
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "VALIDATION"
    reversed_range = await client.get("/api/v1/dashboard/stats",
                                      params={"from": "2026-10-20T00:00:00Z", "to": "2026-10-10T00:00:00Z"},
                                      headers=await auth.headers("maria"))
    assert reversed_range.status_code == 400
    granularity = await client.get("/api/v1/dashboard/stats", params={**_range(1), "granularity": "week"},
                                   headers=await auth.headers("maria"))
    assert granularity.status_code == 400


@pytest.mark.parametrize("report_type", ["alerts-by-day", "events-by-class", "vehicle-activity", "false-alarms"])
async def test_reports_json_and_csv(client, auth, report_type):
    headers = await auth.headers("maria")
    as_json = await client.get(f"/api/v1/reports/{report_type}", params=_range(14), headers=headers)
    assert as_json.status_code == 200, as_json.text
    body = as_json.json()
    assert body["columns"] and body["total"] == len(body["rows"])
    assert all(len(row) == len(body["columns"]) for row in body["rows"])
    as_csv = await client.get(f"/api/v1/reports/{report_type}", params={**_range(14), "format": "csv"}, headers=headers)
    assert as_csv.status_code == 200
    assert as_csv.headers["content-type"].startswith("text/csv")
    assert "attachment" in as_csv.headers["content-disposition"]
    lines = as_csv.text.strip().splitlines()
    assert lines[0] == ",".join(body["columns"])
    assert len(lines) == body["total"] + 1


async def test_report_vehicle_filter_and_unknown_type(client, auth):
    headers = await auth.headers("maria")
    own = await client.get("/api/v1/reports/vehicle-activity", params={**_range(14), "vehicleId": "AV-12"}, headers=headers)
    assert own.status_code == 200 and [r[0] for r in own.json()["rows"]] == ["AV-12"]
    other = await client.get("/api/v1/reports/vehicle-activity", params={**_range(14), "vehicleId": "AV-01"}, headers=headers)
    assert other.status_code == 404
    unknown = await client.get("/api/v1/reports/nope", params=_range(1), headers=headers)
    assert unknown.status_code == 404
    assert (await client.get("/api/v1/reports/false-alarms", params=_range(1),
                             headers=await auth.headers("iot"))).status_code == 403
