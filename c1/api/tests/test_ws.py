"""WebSocket /ws: auth handshake (4401 on a bad token) and the fan-out of
EventBus events to the right rooms only. Uses its own app instance under
Starlette's TestClient, which runs the lifespan and the sockets in one loop."""
from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from conftest import PASSWORD, USERS, install_fakes
from conftest import test_settings as make_settings


@pytest.fixture(scope="module")
def ws_client():
    application = create_app(make_settings())
    install_fakes(application.state.container)
    with TestClient(application) as client:
        yield client


def _token(client: TestClient, who: str) -> str:
    response = client.post("/api/v1/auth/login", json={"email": USERS[who], "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["accessToken"]


def _service_headers(client: TestClient) -> dict:
    return {"Authorization": f"Bearer {client.app.state.container.tokens.issue_service_token('test')}"}


def test_bad_token_closes_with_4401(ws_client):
    with ws_client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": "not-a-jwt"}))
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
        assert closed.value.code == 4401


def test_first_message_must_be_auth(ws_client):
    with ws_client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "pong"}))
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
        assert closed.value.code == 4401


def test_fan_out_to_tenant_rooms_and_staff(ws_client):
    hub = ws_client.app.state.container.hub
    with ws_client.websocket_connect("/ws") as anh, ws_client.websocket_connect("/api/v1/ws") as maria, \
            ws_client.websocket_connect("/ws") as staff:
        anh.send_text(json.dumps({"type": "auth", "token": _token(ws_client, "anh")}))
        maria.send_text(json.dumps({"type": "auth", "token": _token(ws_client, "maria")}))
        staff.send_text(json.dumps({"type": "auth", "token": _token(ws_client, "staff")}))
        # Internal publish, as the separate C3 container would do it.
        alert_a = {"alertId": 90001, "vehicleId": "AV-01", "type": "ACCIDENT", "severity": "CRITICAL", "status": "NEW",
                   "topClass": "crash", "confidence": 0.93, "createdAt": "2026-11-04T21:42:12Z"}
        alert_b = {"alertId": 90002, "vehicleId": "AV-12", "type": "SECURITY", "severity": "HIGH", "status": "NEW",
                   "topClass": "glass_break", "confidence": 0.8, "createdAt": "2026-11-04T21:43:00Z"}
        # Wait until the three sockets joined their rooms.
        import time
        deadline = time.time() + 5
        while (hub.connection_count("tenant:1") < 1 or hub.connection_count("tenant:2") < 1
               or hub.connection_count("staff") < 1) and time.time() < deadline:
            time.sleep(0.05)
        headers = _service_headers(ws_client)
        r1 = ws_client.post("/api/v1/internal/events", headers=headers,
                            json={"topic": "alert.created", "payload": {"alert": alert_a}, "tenantId": 1})
        assert r1.status_code == 202
        r2 = ws_client.post("/api/v1/internal/events", headers=headers,
                            json={"topic": "alert.created", "payload": {"alert": alert_b}, "tenantId": "tB"})
        assert r2.status_code == 202
        r3 = ws_client.post("/api/v1/internal/events", headers=headers,
                            json={"topic": "alert.updated", "payload": {"alertId": 90001, "status": "ACKNOWLEDGED",
                                                                        "by": "Anh Nguyen"}, "tenantId": 1})
        assert r3.status_code == 202
        r4 = ws_client.post("/api/v1/internal/events", headers=headers,
                            json={"topic": "model.activated", "payload": {"modelId": 1, "version": "1.2.0"}})
        assert r4.status_code == 202

        # Tenant A gets its alert and the update, never tenant B's alert, and no tenantId field.
        first = anh.receive_json()
        assert first == {"type": "alert.created", "alert": alert_a}
        second = anh.receive_json()
        assert second == {"type": "alert.updated", "alertId": 90001, "status": "ACKNOWLEDGED", "by": "Anh Nguyen"}
        # Tenant B's first message is its own alert (it did not receive tenant A's).
        assert maria.receive_json() == {"type": "alert.created", "alert": alert_b}
        # Staff receive everything plus model.activated.
        assert staff.receive_json()["alert"]["alertId"] == 90001
        assert staff.receive_json()["alert"]["alertId"] == 90002
        assert staff.receive_json()["type"] == "alert.updated"
        assert staff.receive_json() == {"type": "model.activated", "modelId": 1, "version": "1.2.0"}


def test_internal_events_need_service_token(ws_client):
    user = _token(ws_client, "admin")
    response = ws_client.post("/api/v1/internal/events", headers={"Authorization": f"Bearer {user}"},
                              json={"topic": "device.status", "payload": {}})
    assert response.status_code == 401
    assert ws_client.post("/api/v1/internal/events", json={"topic": "device.status", "payload": {}}).status_code == 401


def test_device_status_forwards_optional_fields(ws_client):
    hub = ws_client.app.state.container.hub
    with ws_client.websocket_connect("/ws") as maria:
        maria.send_text(json.dumps({"type": "auth", "token": _token(ws_client, "maria")}))
        import time
        deadline = time.time() + 5
        while hub.connection_count("tenant:2") < 1 and time.time() < deadline:
            time.sleep(0.05)
        payload = {"vehicleId": "AV-13", "deviceId": "dev-1312", "status": "FAULT", "lastSeenAt": "2026-10-30T10:00:00Z",
                   "faultReason": "mic unplugged", "muted": False, "config": {"version": 3, "state": "CONFIRMED"}}
        ws_client.post("/api/v1/internal/events", headers=_service_headers(ws_client),
                       json={"topic": "device.status", "payload": payload, "tenantId": 2})
        assert maria.receive_json() == {"type": "device.status", **payload}
