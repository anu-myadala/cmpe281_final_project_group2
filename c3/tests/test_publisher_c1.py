"""Integration: C3's HttpPublisher against the REAL C1 app, in this process.

C1 is built exactly as its own WebSocket test builds it (c1/api/tests/test_ws.py): C1's
create_app with C1's test_settings and install_fakes, imported from c1/api/tests/conftest.py
(not changed), under Starlette's TestClient, which runs C1's lifespan and its sockets in one
event loop. C3's publisher runs in that same loop (client.portal) and reaches C1 through
httpx.ASGITransport. Checked: C1 answers 2xx, and the WebSocket clients in the tenant's
room and the staff room receive the event (without tenantId), and another tenant does not.
"""
from __future__ import annotations

import importlib.util
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from c3_service.services.publisher import HttpPublisher

C1_TESTS = Path(__file__).resolve().parents[2] / "c1" / "api" / "tests" / "conftest.py"


def _c1_test_helpers():
    """C1's own test conftest, loaded under another module name so it can't clash with C3's conftest."""
    spec = importlib.util.spec_from_file_location("c1_tests_conftest", C1_TESTS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _receive(ws, seconds: float = 5.0):
    """ws.receive_json() that fails the test after `seconds` instead of hanging."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(ws.receive_json).result(timeout=seconds)


def _wait_rooms(hub, *rooms: str) -> None:
    deadline = time.time() + 5
    while any(hub.connection_count(r) < 1 for r in rooms) and time.time() < deadline:
        time.sleep(0.05)
    assert all(hub.connection_count(r) >= 1 for r in rooms), "the sockets did not join their rooms"


@pytest.fixture(scope="module")
def c1(tmp_path_factory):
    helpers = _c1_test_helpers()
    from app.main import create_app as c1_create_app
    db = tmp_path_factory.mktemp("c1") / "c1.db"
    application = c1_create_app(helpers.test_settings(database_url=f"sqlite+aiosqlite:///{db}"))
    helpers.install_fakes(application.state.container)
    with TestClient(application) as client:
        yield client, helpers


def _login(client: TestClient, helpers, who: str) -> str:
    response = client.post("/api/v1/auth/login", json={"email": helpers.USERS[who], "password": helpers.PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["accessToken"]


def test_c1_accepts_alert_updated_and_pushes_it_to_the_tenant_room(c1):
    client, helpers = c1
    container = client.app.state.container
    publisher = HttpPublisher("http://c1.test/api/v1", container.settings.service_token_secret,
                              transport=httpx.ASGITransport(app=client.app))
    status = {}
    with client.websocket_connect("/ws") as anh, client.websocket_connect("/ws") as maria, \
            client.websocket_connect("/ws") as staff:
        for ws, who in ((anh, "anh"), (maria, "maria"), (staff, "staff")):
            ws.send_text(json.dumps({"type": "auth", "token": _login(client, helpers, who)}))
        _wait_rooms(container.hub, "tenant:1", "tenant:2", "staff")

        updated = {"alertId": 20482, "status": "ACKNOWLEDGED", "by": "Anh Nguyen", "action": "ACKNOWLEDGE",
                   "vehicleId": "AV-01"}
        status["tenant1"] = client.portal.call(publisher.publish, "alert.updated", updated, 1)
        # A second event, for tenant 2, so Maria's FIRST message shows she never got tenant 1's.
        created = {"alert": {"alertId": 30001, "vehicleId": "AV-12", "type": "SECURITY", "severity": "HIGH",
                             "status": "NEW", "topClass": "glass_break", "confidence": 0.8,
                             "createdAt": "2026-11-06T10:00:00Z", "lat": 37.33, "lon": -121.88}}
        status["tenant2"] = client.portal.call(publisher.publish, "alert.created", created, 2)

        assert status == {"tenant1": True, "tenant2": True}  # publish() is True only on a 2xx from C1
        assert _receive(anh) == {"type": "alert.updated", **updated}          # no tenantId on the socket
        assert _receive(maria) == {"type": "alert.created", **created}
        assert _receive(staff) == {"type": "alert.updated", **updated, "tenantId": 1}
        assert _receive(staff) == {"type": "alert.created", **created, "tenantId": 2}


def test_c1_answers_2xx_to_the_c3_token(c1):
    """The same request, sent directly, to read C1's status code: 202 Accepted."""
    client, _ = c1
    publisher = HttpPublisher("http://c1.test/api/v1", client.app.state.container.settings.service_token_secret)
    response = client.post("/api/v1/internal/events", headers={"Authorization": f"Bearer {publisher.service_token()}"},
                           json={"topic": "alert.updated", "payload": {"tenantId": 1, "alertId": 1, "status": "RESOLVED",
                                                                       "by": "x"}, "tenantId": 1})
    assert response.status_code == 202


def test_c1_refuses_a_token_signed_with_another_secret(c1):
    """And C3 only logs that refusal: publish() returns False, nothing is raised."""
    client, _ = c1
    publisher = HttpPublisher("http://c1.test/api/v1", "not-the-service-secret-but-long-enough-for-hs256",
                              transport=httpx.ASGITransport(app=client.app))
    assert client.portal.call(publisher.publish, "alert.updated", {"alertId": 1, "status": "RESOLVED"}, 1) is False
