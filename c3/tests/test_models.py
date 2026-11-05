"""Step 2: the models.

* alert and alert_action are identical to C1's: the class source text, character for
  character, and the DDL both produce on MySQL and SQLite.
* C3 creates only its three tables, seeds them idempotently, and the 7 default rules
  are those of stubs/common.py ALERT_RULES.
* C3's mappings read the rows the C1 seed wrote.
"""
from __future__ import annotations

import ast
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateIndex, CreateTable

from c3_service.db import make_engine
from c3_service.models import Alert, AlertAction, AlertRule, AlertType, SoundClass, sound_class_name
from c3_service.models import Base as C3Base
from c3_service.schema import C3_TABLES, create_c3_schema

REPO_ROOT = Path(__file__).resolve().parents[2]
C1_MODELS = REPO_ROOT / "c1" / "api" / "app" / "models" / "c2c3.py"
C3_MODELS = REPO_ROOT / "c3" / "c3_service" / "models" / "alert.py"


def _class_source(path: Path, name: str) -> str:
    source = path.read_text()
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == name)
    return ast.get_source_segment(source, node)


@pytest.mark.parametrize("name", ["Alert", "AlertAction"])
def test_class_source_matches_c1_character_for_character(name):
    assert _class_source(C3_MODELS, name) == _class_source(C1_MODELS, name)


@pytest.mark.parametrize("table", ["alert", "alert_action"])
@pytest.mark.parametrize("dialect", [mysql.dialect(), sqlite.dialect()], ids=["mysql", "sqlite"])
def test_ddl_matches_c1(table, dialect):
    from app.models import Base as C1Base  # C1's own models
    c1_table, c3_table = C1Base.metadata.tables[table], C3Base.metadata.tables[table]

    def ddl(t):
        return [str(CreateTable(t).compile(dialect=dialect))] + sorted(
            str(CreateIndex(i).compile(dialect=dialect)) for i in t.indexes)

    assert ddl(c3_table) == ddl(c1_table)


def test_c3_creates_only_its_own_tables():
    assert {t.name for t in C3_TABLES} == {"alert_rule", "notification", "sound_class"}


async def test_fresh_database_gets_only_the_three_tables():
    """On an empty database C3 creates its three tables and nothing of C1's."""
    path = Path(tempfile.gettempdir()) / "scicp_c3_empty.db"
    path.unlink(missing_ok=True)
    engine = make_engine(f"sqlite+aiosqlite:///{path}")
    try:
        counts = await create_c3_schema(engine)
        async with engine.connect() as conn:
            names = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    finally:
        await engine.dispose()
        path.unlink(missing_ok=True)
    assert names == {"alert_rule", "notification", "sound_class"}
    assert counts == {"soundClasses": 7, "alertRules": 7}


async def test_seed_is_idempotent(app):
    assert await create_c3_schema(app.state.engine) == {"soundClasses": 7, "alertRules": 7}


async def test_default_rules_match_the_stub(app):
    """Rule ids 1..7 and their fields are exactly stubs/common.py ALERT_RULES."""
    from stubs.common import ALERT_RULES
    async with app.state.session_factory() as session:
        rows = (await session.execute(select(AlertRule, AlertType.default_severity)
                                      .join(AlertType, AlertType.code == AlertRule.alert_type_code)
                                      .order_by(AlertRule.rule_id))).all()
    ours = [{"ruleId": r.rule_id, "scope": "DEFAULT" if r.tenant_id is None else "TENANT", "tenantId": r.tenant_id,
             "alertType": r.alert_type_code, "class": sound_class_name(r.class_id), "vehicleState": r.vehicle_state,
             "minConfidence": float(r.min_confidence), "severity": r.severity_override or default_severity,
             "severityOverride": r.severity_override, "minRepeats": r.min_repeats, "windowSeconds": r.window_s,
             "enabled": r.enabled} for r, default_severity in rows]
    assert ours == ALERT_RULES


async def test_sound_classes_use_the_seed_ids(app):
    from app.models import SOUND_CLASSES  # C1's list: class_id = position + 1
    async with app.state.session_factory() as session:
        rows = (await session.execute(select(SoundClass).order_by(SoundClass.class_id))).scalars().all()
    assert [(r.class_id, r.name) for r in rows] == [(i + 1, n) for i, n in enumerate(SOUND_CLASSES)]


async def test_one_default_per_class_state_and_type(app):
    """The generated tenant_key makes the unique key work for tenant_id NULL (Table 94)."""
    async with app.state.session_factory() as session:
        session.add(AlertRule(tenant_id=None, alert_type_code="ACCIDENT", class_id=2, vehicle_state="MOVING",
                              min_confidence=0.5, min_repeats=1, enabled=True))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        # A tenant override of the same rule is allowed, and the default stays.
        session.add(AlertRule(tenant_id=2, alert_type_code="ACCIDENT", class_id=2, vehicle_state="MOVING",
                              min_confidence=0.8, min_repeats=1, enabled=True))
        await session.commit()
        override = (await session.execute(select(AlertRule).where(AlertRule.tenant_id == 2))).scalar_one()
        assert override.tenant_key == 2 and override.rule_id > 7
        await session.delete(override)
        await session.commit()


async def test_reads_the_seeded_alert(app):
    async with app.state.session_factory() as session:
        alert = await session.get(Alert, 20482)
        actions = (await session.execute(select(AlertAction).where(AlertAction.alert_id == 20482))).scalars().all()
    assert (alert.vehicle_id, alert.alert_type_code, alert.severity, alert.status) == ("AV-01", "ACCIDENT", "CRITICAL", "NEW")
    assert (sound_class_name(alert.top_class_id), float(alert.confidence), alert.rule_id) == ("crash", 0.91, 2)
    assert alert.event_ref == "6729b1c4e1f0a2d3c4b5a697"
    assert [(a.action, a.user_id, a.to_status) for a in actions] == [("CREATE", None, "NEW")]
