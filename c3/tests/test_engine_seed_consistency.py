"""Does the engine agree with the decisions the seed stored? (No forcing: mismatches are reported.)

For every seeded audio event with a stored decision, AlertEngine.decide() (which only reads)
runs on a scratch copy of the seeded database, with the event's stored top class and
confidence, its stored context, and the seeded rules and subscriptions. Compared with the
stored decision: alert yes/no, alert type and severity. The reason is shown for information.
Known seed artifacts (decided 2026-11-06): seed/seed.py builds its alert history without
applying the rules, so 17 seeded alerts would not be created by the engine. They are pinned in
KNOWN_SEED_ARTIFACTS with the engine's reason. The test fails on any other mismatch, on a pinned
alert whose reason changes, and on a pinned alert that starts to match (so the list stays exact).
"""
from __future__ import annotations

from collections import Counter

from c3_service.db import make_engine, make_session_factory
from c3_service.services.alert_engine import AlertEngine
from c3_service.services.event_store import ResultEvents
from c3_service.services.rule_resolver import state_of
from conftest import sqlite_url

TENANT_IDS = {"tA": 1, "tB": 2}

# alertId -> the engine's reason. Why each group differs from the seed:
KNOWN_SEED_ARTIFACTS = {
    # honking from a single event: BR-C3-05 needs 2 honking events within 10 s
    20443: "REPEAT_PENDING", 20446: "REPEAT_PENDING", 20452: "REPEAT_PENDING", 20455: "REPEAT_PENDING",
    20457: "REPEAT_PENDING", 20458: "REPEAT_PENDING", 20460: "REPEAT_PENDING", 20461: "REPEAT_PENDING",
    20465: "REPEAT_PENDING", 20476: "REPEAT_PENDING", 20478: "REPEAT_PENDING",
    # honking on AV-12, where the seed's own subscription has SAFETY off
    20444: "SERVICE_OFF", 20451: "SERVICE_OFF",
    # ACCIDENT with confidence 0.632 - 0.671, under the 0.70 minimum of rules 2 and 3
    20441: "BELOW_THRESHOLD", 20453: "BELOW_THRESHOLD", 20454: "BELOW_THRESHOLD", 20480: "BELOW_THRESHOLD",
}


async def _mismatches(fresh_db_copy, mongo, app):
    engine_db = make_engine(sqlite_url(fresh_db_copy("consistency.db")))
    engine = AlertEngine(make_session_factory(engine_db), ResultEvents(mongo), None, app.state.tenants)
    rows, checked = [], 0
    try:
        async for event in mongo["audio_events"].find({"decision": {"$exists": True}}).sort("ts", 1):
            stored = event["decision"]
            top = event["inference"]["topK"][0]
            ours, _, state = await engine.decide(event, TENANT_IDS[event["tenantId"]], [(top["class"], top["conf"])])
            checked += 1
            key = lambda d: (bool(d.get("alert")), d.get("alertType"), d.get("severity"))  # noqa: E731
            if key(ours) != key(stored):
                rows.append({"event": str(event["_id"]), "alertId": stored.get("alertId"), "reason": ours["reason"], "tenant": event["tenantId"],
                             "vehicle": event["vehicleId"], "class": top["class"], "conf": top["conf"], "state": state,
                             "speed": (event.get("context") or {}).get("speedKph"),
                             "stored": f"{'ALERT ' + stored['alertType'] + '/' + stored['severity'] if stored.get('alert') else 'no alert'}"
                                       f" ({stored.get('reason')})",
                             "engine": f"{'ALERT ' + ours['alertType'] + '/' + ours['severity'] if ours.get('alert') else 'no alert'}"
                                       f" ({ours['reason']})"})
    finally:
        await engine_db.dispose()
    return rows, checked


def _table(rows) -> str:
    header = "| # | alertId | event | tenant | vehicle | class | conf | state | stored decision | engine decision |"
    lines = [header, "|" + "---|" * 10]
    for i, r in enumerate(rows, 1):
        lines.append(f"| {i} | {r['alertId'] or ''} | {r['event'][-6:]} | {r['tenant']} | {r['vehicle']} | {r['class']} | "
                     f"{r['conf']:.3f} | {r['state']} | {r['stored']} | {r['engine']} |")
    summary = Counter((r["stored"].split(" (")[0].split("/")[0], r["engine"].split("(")[1].rstrip(")")) for r in rows)
    lines.append("")
    lines += [f"{n} x stored {s} -> engine {e}" for (s, e), n in sorted(summary.items())]
    return "\n".join(lines)


async def test_engine_agrees_with_the_seeded_decisions(fresh_db_copy, mongo, app):
    rows, checked = await _mismatches(fresh_db_copy, mongo, app)
    assert checked == 401
    unexpected = [r for r in rows if KNOWN_SEED_ARTIFACTS.get(r["alertId"]) != r["reason"]]
    assert not unexpected, f"{len(unexpected)} seeded decisions differ from the engine:\n{_table(unexpected)}"
    now_matching = sorted(set(KNOWN_SEED_ARTIFACTS) - {r["alertId"] for r in rows})
    assert not now_matching, f"pinned seed artifacts that now match the engine (remove them): {now_matching}"
