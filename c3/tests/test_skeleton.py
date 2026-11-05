"""Step 1: the service starts, answers its health check, and refuses in the platform's error envelope."""
from __future__ import annotations

from datetime import datetime

import pytest

from c3_service.errors import Validation
from c3_service.timeutil import iso, parse_iso


async def test_health(client, api):
    response = await client.get(f"{api}/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["component"] == "C3" and body["database"] == "ok"


async def test_unknown_route_uses_the_error_envelope(client, api):
    response = await client.get(f"{api}/no-such-route")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "NOT_FOUND", "message": "Not Found"}}


def test_iso_matches_c1_format():
    assert iso(datetime(2026, 11, 4, 21, 42, 12)) == "2026-11-04T21:42:12Z"
    assert iso(datetime(2026, 11, 4, 21, 42, 7, 312000)) == "2026-11-04T21:42:07.312Z"
    assert iso(None) is None


def test_parse_iso():
    assert parse_iso("2026-11-04T21:42:12Z") == datetime(2026, 11, 4, 21, 42, 12)
    assert parse_iso("2026-11-04") == datetime(2026, 11, 4)
    with pytest.raises(Validation):
        parse_iso("yesterday", "from")
