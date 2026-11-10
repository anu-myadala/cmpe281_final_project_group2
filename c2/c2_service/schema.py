"""Creates C2's own tables and gives the seeded cars their devices. Idempotent.

Only device, device_command, network_config and clip are created here (C2_TABLES); vehicle
and vehicle_rider are created and seeded by C1's schema and seed/seed.py in this repository.
The stub invented three devices per car (dev-NN10 gateway, dev-NN11 interior microphone,
dev-NN12 exterior microphone); C2 stores exactly those for every seeded car that has none,
with the last-seen time of the car's newest telemetry sample, so the dashboard looks the same
the moment the stub is replaced.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from .db import make_session_factory
from .models import Base, Clip, Device, DeviceCommand, NetworkConfig, Tenant, Vehicle
from .services.commands import DEFAULT_MIC_CONFIG
from .services.registry import default_position
from .services.status import decide_status
from .services.support import latest_samples
from .timeutil import as_naive_utc, utcnow

log = logging.getLogger("scicp.c2.schema")

C2_TABLES = (Device.__table__, DeviceCommand.__table__, NetworkConfig.__table__, Clip.__table__)
SEED_DEVICES = (("10", "EDGE_GATEWAY"), ("11", "MIC_INTERIOR"), ("12", "MIC_EXTERIOR"))


async def create_c2_schema(engine: AsyncEngine, mongo: Any, offline_after_s: int = 30) -> Dict[str, int]:
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=list(C2_TABLES)))
    created = 0
    async with make_session_factory(engine)() as session:
        have = set((await session.execute(select(Device.vehicle_id))).scalars())
        codes = {t.tenant_id: t.code for t in (await session.execute(select(Tenant))).scalars()}
        vehicles = [v for v in (await session.execute(select(Vehicle).order_by(Vehicle.vehicle_id))).scalars()
                    if v.vehicle_id not in have]
        samples: Dict[str, Dict[str, Any]] = {}
        for tenant_code in {codes.get(v.tenant_id) for v in vehicles}:
            if tenant_code:
                for code, doc in (await latest_samples(mongo, tenant_code)).items():
                    samples[f"{tenant_code}/{code}"] = doc
        now = utcnow()
        for v in vehicles:
            number = int(v.vehicle_code.split("-")[-1])
            sample = samples.get(f"{codes.get(v.tenant_id)}/{v.vehicle_code}")
            seen = as_naive_utc(sample.get("ts")) if sample else None
            for suffix, kind in SEED_DEVICES:
                mic = kind != "EDGE_GATEWAY"
                d = Device(tenant_id=v.tenant_id, vehicle_id=v.vehicle_id, device_code=f"dev-{number:02d}{suffix}",
                           device_type=kind, position=default_position(kind),
                           serial_no=f"SN-{codes.get(v.tenant_id, 't')}-{v.vehicle_code}-{suffix}",
                           firmware_version="edge-agent 0.4.1" if not mic else "mic-fw 1.2",
                           status="DISABLED" if v.status == "RETIRED" else "PROVISIONING", muted=False,
                           last_seen_at=seen if v.status != "REGISTERED" else None, reported_ok=True,
                           config_json=dict(DEFAULT_MIC_CONFIG) if mic else None, config_version=1,
                           config_state="CONFIRMED" if mic else "NONE", created_at=now)
                d.status = decide_status(d, now, offline_after_s, False)
                session.add(d)
                created += 1
        await session.commit()
    log.info("C2 schema ready on %s: %d devices created for the seeded cars", engine.dialect.name, created)
    return {"devicesCreated": created}
