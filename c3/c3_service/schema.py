"""Creates C3's own tables and seeds them at startup. Idempotent.

Only alert_rule, notification and sound_class are created here (C3_TABLES). alert,
alert_action and v_alert_summary are C1's to create, by the team's decision, and C3
never issues DDL for them. Seeding inserts the missing rows only, so a default
rule that a SYS_ADMIN edits later is not reset by a restart.

On MySQL the foreign keys of the new tables need C1's tenant, alert_type, app_user
and alert tables, so C3 starts after the seed has run (docker compose: depends_on seed).
"""
from __future__ import annotations

import logging
from typing import Dict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from .db import make_session_factory
from .models import CLASS_IDS, DEFAULT_RULES, SOUND_CLASS_SEED, AlertRule, Base, Notification, SoundClass

log = logging.getLogger("scicp.c3.schema")

# In dependency order: alert_rule references sound_class.
C3_TABLES = (SoundClass.__table__, AlertRule.__table__, Notification.__table__)


async def create_c3_schema(engine: AsyncEngine) -> Dict[str, int]:
    """CREATE TABLE IF NOT EXISTS for the three C3 tables, then seed the 7 classes and the 7 default rules."""
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=list(C3_TABLES)))
    async with make_session_factory(engine)() as session:
        have_classes = set((await session.execute(select(SoundClass.class_id))).scalars())
        for class_id, name, category, mic in SOUND_CLASS_SEED:
            if class_id not in have_classes:
                session.add(SoundClass(class_id=class_id, name=name, category=category, default_mic=mic))
        await session.flush()
        have_rules = set((await session.execute(select(AlertRule.rule_id))).scalars())
        for rule_id, alert_type, cls, state, min_conf, repeats, window_s, enabled in DEFAULT_RULES:
            if rule_id not in have_rules:
                session.add(AlertRule(rule_id=rule_id, tenant_id=None, alert_type_code=alert_type,
                                      class_id=CLASS_IDS[cls], vehicle_state=state, min_confidence=min_conf,
                                      severity_override=None, min_repeats=repeats, window_s=window_s,
                                      enabled=enabled, updated_by=None, updated_at=None))
        await session.commit()
        counts = {
            "soundClasses": int((await session.execute(select(func.count()).select_from(SoundClass))).scalar_one()),
            "alertRules": int((await session.execute(select(func.count()).select_from(AlertRule))).scalar_one()),
        }
    log.info("C3 schema ready on %s: %s", engine.dialect.name, counts)
    return counts
