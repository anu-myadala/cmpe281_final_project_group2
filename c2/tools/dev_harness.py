"""Developer harness: C2 without Docker, for working on C2 alone on a laptop.

One process serves C2 under /c2 (exactly the paths nginx gives it: /c2/api/v1, /c2/console/) and
a minimal stand-in for the C1 calls C2 and its console need (login, tenants, internal events,
subscription defaults). The database is a seeded SQLite file and MongoDB is the in-process mock.
You still need an MQTT broker on localhost:1883 (Mosquitto, or `amqtt` from pip).

    cd c2
    python tools/dev_harness.py            # http://127.0.0.1:8090/c2/console/
    python edge/simulator.py               # with C1_BASE_URL=http://127.0.0.1:8090/api/v1
                                           #      C2_URL=http://127.0.0.1:8090/c2/api/v1 SIM_MQTT_HOST=localhost

This is not part of the platform: in docker compose C1 is the real C1.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
import uuid
from pathlib import Path

import jwt
import uvicorn
from fastapi import FastAPI, HTTPException, Request

C2_DIR = Path(__file__).resolve().parents[1]
REPO = C2_DIR.parent
for p in (str(C2_DIR), str(REPO), str(REPO / "c1" / "api")):
    if p not in sys.path:
        sys.path.insert(0, p)

PORT = int(os.environ.get("HARNESS_PORT", "8090"))
DB = Path(os.environ.get("HARNESS_DB", "/tmp/scicp_c2_harness.db"))
os.environ.setdefault("JWT_SECRET", "dev-jwt-secret-0123456789abcdef0123456789")
os.environ.setdefault("SERVICE_TOKEN_SECRET", "dev-service-secret-0123456789abcdef0123")

from c2_service.config import Settings  # noqa: E402
from c2_service.main import create_app  # noqa: E402

USERS = {"anh@example.com": (1001, "OWNER", 1, "Anh Nguyen"), "maria@bayshuttle.example": (1002, "FLEET_MANAGER", 2, "Maria Lopez"),
         "rider@example.com": (1003, "RIDER", 1, "Rider Demo"), "iot@scicp.example": (1004, "IOT_ENGINEER", None, "D. Patel"),
         "staff@scicp.example": (1005, "SERVICE_STAFF", None, "Service Staff"), "admin@scicp.example": (1006, "SYS_ADMIN", None, "Platform Admin")}
EVENTS: list = []


def build() -> FastAPI:
    from mongomock_motor import AsyncMongoMockClient
    from seed.seed import run as seed_run

    mongo = AsyncMongoMockClient()["scicp"]
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(f"{DB}{suffix}").unlink(missing_ok=True)
    url = f"sqlite+aiosqlite:///{DB}"
    asyncio.run(seed_run(url, mongo, storage=None))
    settings = Settings(_env_file=None, database_url=url, mongo_url="mock", c1_base_url=f"http://127.0.0.1:{PORT}/api/v1",
                        mqtt_url=os.environ.get("MQTT_URL", "mqtt://127.0.0.1:1883"), c2_clip_dir="/tmp/scicp_c2_clips",
                        c2_public_api_base=f"http://127.0.0.1:{PORT}/c2/api/v1", log_level="INFO")
    c2 = create_app(settings, mongo=mongo)
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_outer):
        async with c2.router.lifespan_context(c2):   # a mounted app's lifespan does not run by itself
            yield

    outer = FastAPI(lifespan=lifespan)

    @outer.post("/api/v1/auth/login")
    async def login(body: dict):
        user = USERS.get(str(body.get("email", "")).lower())
        if user is None or body.get("password") != "Passw0rd!demo":
            raise HTTPException(401, "Wrong email or password")
        uid, role, tenant, name = user
        now = int(time.time())
        token = jwt.encode({"sub": str(uid), "tenantId": tenant, "role": role, "name": name, "jti": uuid.uuid4().hex,
                            "iat": now, "exp": now + 8 * 3600}, settings.jwt_secret, algorithm="HS256")
        return {"accessToken": token, "expiresIn": 8 * 3600,
                "user": {"userId": uid, "email": body["email"], "name": name, "role": role, "tenantId": tenant}}

    @outer.get("/api/v1/admin/tenants")
    async def tenants():
        return {"items": [{"id": 1, "code": "tA", "name": "Nguyen household", "plan": "BASIC", "maxVehicles": 3},
                          {"id": 2, "code": "tB", "name": "Bay Shuttle LLC", "plan": "FLEET", "maxVehicles": 50}]}

    @outer.post("/api/v1/internal/events", status_code=202)
    async def events(body: dict):
        EVENTS.append(body)
        logging.getLogger("harness").info("event %s %s", body.get("topic"), body.get("payload"))
        return None

    @outer.post("/api/v1/internal/subscriptions/defaults", status_code=201)
    async def defaults(body: dict):
        return {"vehicleId": body.get("vehicleId"), "created": 4}

    @outer.get("/api/v1/harness/events")
    async def recent_events():
        return EVENTS[-50:]

    outer.mount("/c2", c2)
    return outer


if __name__ == "__main__":
    uvicorn.run(build(), host="127.0.0.1", port=PORT, log_level="info")
