"""Contract check of the real C2 against what C3 and the coming C4 worker need.

Read-only toward C2. Against the running stack (docker-compose.yml + c3 + c2 + c2c3 overlays) it:

  1. captures N messages from scicp/v1/+/+/audio/event (paho-mqtt, MQTT login from .env) and validates
     each against the event contract (Part II Table 46 / Part III Table 86);
  2. fetches every clip with GET /internal/clips/{clipId} (service token) and checks 200, audio/wav and
     the WAV format (mono, 16 kHz, 16-bit, 5.0 +/- 0.1 s); saves them to $SCICP_DATA/work/c2_clips/;
     also checks 401 without a token, 403 with a user token, 404 for an unknown clipId;
  3. reports whether C2 stored each event in MongoDB audio_events (fields, inference key);
  4. posts a result for one event to C3 (edge.class / edge.score as the single class), creating the
     audio_events document first only if C2 did not, and checks the alert appears in
     GET /c3/api/v1/alerts through nginx as the tenant's user;
  5. prints a summary and every mismatch against the contract. Exit code 1 if any check FAILs.

    c3/.venv/bin/python c3/tools/check_c2.py            # --count 5 --timeout 300
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import sys
import threading
import time
import uuid
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
import paho.mqtt.client as mqtt
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from send_result import database_name, service_token  # noqa: E402  (reuse the C4 tool's token and DB name)

REPO = Path(__file__).resolve().parents[2]
TOPIC = "scicp/v1/+/+/audio/event"
CLASSES = {"siren", "crash", "glass_break", "scream", "honking", "traffic", "environmental"}
USERS = {"tA": "anh@example.com", "tB": "maria@bayshuttle.example"}
DEMO_PASSWORD = "Passw0rd!demo"
CONTRACT = {
    "eventId": None, "deviceId": None, "channel": None, "ts": None,
    "clip": {"clipId", "s3Key", "durationMs", "sampleRate"},
    "edge": {"class", "score", "priority"},
    "location": {"type", "coordinates"},
    "context": {"speedKph", "driveMode", "ignition"},
}

PASS, FAIL, NA, WARN = "PASS", "FAIL", "N/A", "WARN"


# ---------------------------------------------------------------------------------------------- utilities
def load_env(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def short(value: Any, n: int = 48) -> str:
    s = value if isinstance(value, str) else json.dumps(value, default=str)
    return s if len(s) <= n else s[: n - 3] + "..."


def table(headers: List[str], rows: List[List[Any]]) -> str:
    rows = [[str(c) for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    line = "+".join("-" * (w + 2) for w in widths)
    fmt = lambda r: " | ".join(c.ljust(w) for c, w in zip(r, widths))  # noqa: E731
    return "\n".join([fmt(headers), line] + [fmt(r) for r in rows])


def is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class Report:
    def __init__(self) -> None:
        self.rows: List[Tuple[str, str, str, str]] = []      # (section, check, result, detail)
        self.mismatches: List[str] = []
        self.notes: List[str] = []

    def add(self, section: str, check: str, ok: Optional[bool], detail: str = "", expected: str = "") -> str:
        result = NA if ok is None else (PASS if ok else FAIL)
        self.rows.append((section, check, result, detail))
        if ok is False:
            self.mismatches.append(f"[{section}] {check}: expected {expected or '(see check)'}; got {detail}")
        return result

    def failed(self) -> bool:
        return any(r[2] == FAIL for r in self.rows)


# ----------------------------------------------------------------------------------------- 1. MQTT capture
def capture(host: str, port: int, user: Optional[str], password: Optional[str], count: int,
            timeout: float) -> Tuple[List[Tuple[str, bytes]], Optional[str]]:
    got: List[Tuple[str, bytes]] = []
    done = threading.Event()
    err: List[str] = []

    def on_connect(client, _ud, _flags, reason_code, _props):
        if reason_code.is_failure:
            err.append(f"connect refused: {reason_code}")
            done.set()
            return
        client.subscribe(TOPIC, qos=1)

    def on_message(_client, _ud, msg):
        if len(got) < count:
            got.append((msg.topic, msg.payload))
            print(f"  [{len(got)}/{count}] {msg.topic}")
        if len(got) >= count:
            done.set()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"c4-contract-check-{uuid.uuid4().hex[:6]}")
    if user:
        client.username_pw_set(user, password)
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(host, port, keepalive=30)
    except OSError as exc:
        return got, f"cannot connect to {host}:{port}: {exc}"
    client.loop_start()
    done.wait(timeout)
    client.loop_stop()
    client.disconnect()
    if err:
        return got, err[0]
    if len(got) < count:
        return got, f"only {len(got)} of {count} messages within {timeout:.0f} s"
    return got, None


def parse_iso_utc(ts: Any) -> Tuple[bool, str]:
    if not isinstance(ts, str):
        return False, f"not a string: {short(ts)}"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return False, f"not ISO 8601: {ts}"
    if dt.tzinfo is None:
        return False, f"no offset: {ts}"
    if dt.utcoffset() != timedelta(0):
        return False, f"offset not UTC: {ts}"
    return True, ts


def validate_event(topic: str, ev: Dict[str, Any]) -> List[Tuple[str, Optional[bool], str, str]]:
    """[(field, ok, actual, expected)]"""
    out: List[Tuple[str, Optional[bool], str, str]] = []
    parts = topic.split("/")
    t_tenant, t_vehicle = (parts[2], parts[3]) if len(parts) >= 6 else (None, None)

    def chk(field: str, ok: bool, actual: Any, expected: str) -> None:
        out.append((field, ok, short(actual), expected))

    v = ev.get("eventId")
    try:
        uuid.UUID(str(v))
        ok = isinstance(v, str)
    except ValueError:
        ok = False
    chk("eventId", ok, v, "UUID string")
    v = ev.get("deviceId")
    chk("deviceId", isinstance(v, str) and bool(re.fullmatch(r"dev-\d{4}", v)), v, "dev-NNNN")
    v = ev.get("channel")
    chk("channel", v in ("interior", "exterior"), v, "interior | exterior")
    ok, detail = parse_iso_utc(ev.get("ts"))
    chk("ts", ok, detail, "ISO 8601 UTC")

    clip = ev.get("clip") if isinstance(ev.get("clip"), dict) else {}
    chk("clip", isinstance(ev.get("clip"), dict), type(ev.get("clip")).__name__, "object")
    v = clip.get("clipId")
    chk("clip.clipId", isinstance(v, str) and bool(v), v, "non-empty string")
    v = clip.get("s3Key")
    chk("clip.s3Key", isinstance(v, str) and bool(v), v, "non-empty string")
    if isinstance(v, str) and t_tenant:
        chk("clip.s3Key tenant/vehicle", t_tenant in v and t_vehicle in v, v, f"contains {t_tenant} and {t_vehicle}")
    v = clip.get("durationMs")
    chk("clip.durationMs", is_num(v) and abs(v - 5000) <= 100, v, "5000 +/- 100")
    v = clip.get("sampleRate")
    chk("clip.sampleRate", v == 16000, v, "16000")

    edge = ev.get("edge") if isinstance(ev.get("edge"), dict) else {}
    chk("edge", isinstance(ev.get("edge"), dict), type(ev.get("edge")).__name__, "object")
    v = edge.get("class")
    chk("edge.class", v in CLASSES, v, "one of " + ",".join(sorted(CLASSES)))
    v = edge.get("score")
    chk("edge.score", is_num(v) and 0 <= v <= 1, v, "number 0..1")
    v = edge.get("priority")
    chk("edge.priority", isinstance(v, str) and bool(v), v, "string")

    loc = ev.get("location") if isinstance(ev.get("location"), dict) else {}
    chk("location.type", loc.get("type") == "Point", loc.get("type"), "Point")
    c = loc.get("coordinates")
    ok = isinstance(c, list) and len(c) == 2 and all(is_num(x) for x in c) \
        and -180 <= c[0] <= 180 and -90 <= c[1] <= 90
    chk("location.coordinates", ok, c, "[lon, lat] numbers in range")
    if ok:
        # the demo cars drive around the Bay Area (lat ~37, lon ~-122): a swap puts -122 in the lat slot
        lon_lat = c[0] < -90 or (30 <= c[1] <= 45 and not (30 <= c[0] <= 45))
        chk("location order [lon, lat]", lon_lat, c, "[lon, lat] (Bay Area: lon ~-122, lat ~37)")

    ctx = ev.get("context") if isinstance(ev.get("context"), dict) else {}
    chk("context", isinstance(ev.get("context"), dict), type(ev.get("context")).__name__, "object")
    v = ctx.get("speedKph")
    chk("context.speedKph", is_num(v) and v >= 0, v, "number >= 0")
    v = ctx.get("driveMode")
    chk("context.driveMode", isinstance(v, str) and bool(v), v, "string")
    v = ctx.get("ignition")
    chk("context.ignition", isinstance(v, bool), v, "boolean")

    chk("topic tenantId == payload", "tenantId" in ev and ev.get("tenantId") == t_tenant,
        f"topic={t_tenant} payload={ev.get('tenantId', '<missing>')}", "equal")
    chk("topic vehicleId == payload", "vehicleId" in ev and ev.get("vehicleId") == t_vehicle,
        f"topic={t_vehicle} payload={ev.get('vehicleId', '<missing>')}", "equal")
    return out


def extra_missing(ev: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    extra, missing = [], []
    for k, sub in CONTRACT.items():
        if k not in ev:
            missing.append(k)
        elif sub and isinstance(ev[k], dict):
            missing += [f"{k}.{s}" for s in sorted(sub) if s not in ev[k]]
            extra += [f"{k}.{s}" for s in ev[k] if s not in sub]
    extra += [k for k in ev if k not in CONTRACT]
    return extra, missing


# --------------------------------------------------------------------------------------------- 2. clips
def check_wav(data: bytes) -> Dict[str, Any]:
    with wave.open(io.BytesIO(data), "rb") as w:
        frames, rate = w.getnframes(), w.getframerate()
        return {"channels": w.getnchannels(), "rate": rate, "width": w.getsampwidth(),
                "seconds": frames / rate if rate else 0.0}


async def login(client: httpx.AsyncClient, web: str, email: str) -> Optional[str]:
    r = await client.post(f"{web}/api/v1/auth/login", json={"email": email, "password": DEMO_PASSWORD})
    if r.status_code != 200:
        print(f"  login {email} -> {r.status_code} {r.text[:200]}")
        return None
    return r.json().get("accessToken")


# ------------------------------------------------------------------------------------------------ main
async def main() -> int:
    env = {**load_env(REPO / ".env"), **os.environ}
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--c2-url", default=env.get("C2_URL", "http://localhost:8012/api/v1"))
    ap.add_argument("--c3-url", default=env.get("C3_URL", "http://localhost:8003/api/v1"))
    ap.add_argument("--web-url", default=env.get("WEB_URL", "http://localhost:8080"))
    ap.add_argument("--mongo-url", default=env.get("CHECK_MONGO_URL", "mongodb://localhost:27017/scicp"))
    ap.add_argument("--mqtt-host", default=env.get("CHECK_MQTT_HOST", "localhost"))
    ap.add_argument("--mqtt-port", type=int, default=int(env.get("CHECK_MQTT_PORT", "1883")))
    args = ap.parse_args()
    secret = env.get("SERVICE_TOKEN_SECRET")
    if not secret:
        print("SERVICE_TOKEN_SECRET not found in .env or the environment", file=sys.stderr)
        return 2
    clip_dir = Path(env.get("SCICP_DATA", "/Users/ray/Documents/scicp-data")) / "work" / "c2_clips"
    c2, c3, web = args.c2_url.rstrip("/"), args.c3_url.rstrip("/"), args.web_url.rstrip("/")
    rep = Report()

    # 1 ------------------------------------------------------------------------------------------------
    mq_user, mq_pass = env.get("MQTT_USERNAME"), env.get("MQTT_PASSWORD")
    print(f"\n== 1. MQTT: {args.count} messages from {TOPIC} on {args.mqtt_host}:{args.mqtt_port} "
          f"(login {mq_user or 'anonymous'}, timeout {args.timeout:.0f} s)")
    msgs, err = capture(args.mqtt_host, args.mqtt_port, mq_user, mq_pass, args.count, args.timeout)
    rep.add("mqtt", f"capture {args.count} messages", err is None, err or f"{len(msgs)} received", f"{args.count} messages")
    events: List[Tuple[str, Dict[str, Any]]] = []
    for i, (topic, raw) in enumerate(msgs, 1):
        try:
            ev = json.loads(raw)
            ok = isinstance(ev, dict)
        except ValueError:
            ev, ok = {}, False
        rep.add(f"event {i}", "payload is a JSON object", ok, short(raw.decode(errors="replace")), "JSON object")
        if not ok:
            continue
        events.append((topic, ev))
        results = validate_event(topic, ev)
        extra, missing = extra_missing(ev)
        print(f"\n-- event {i}: {topic}\n{json.dumps(ev, indent=None)}")
        print(table(["field", "result", "actual", "expected"],
                    [[f, PASS if ok else FAIL, a, e] for f, ok, a, e in results]))
        print(f"   extra fields:   {', '.join(extra) or '-'}")
        print(f"   missing fields: {', '.join(missing) or '-'}")
        for f, ok, a, e in results:
            rep.add(f"event {i}", f, ok, a, e)
        if missing:
            rep.add(f"event {i}", "no missing contract fields", False, ", ".join(missing), "all contract fields present")
        if extra:
            rep.notes.append(f"event {i}: extra fields (not in the contract): {', '.join(extra)}")

    async with httpx.AsyncClient(timeout=15) as client:
        # 2 --------------------------------------------------------------------------------------------
        print(f"\n== 2. GET {c2}/internal/clips/{{clipId}} (service token); saving to {clip_dir}")
        clip_dir.mkdir(parents=True, exist_ok=True)
        svc = {"Authorization": f"Bearer {service_token(secret)}"}
        clip_rows = []
        for i, (topic, ev) in enumerate(events, 1):
            clip_id = (ev.get("clip") or {}).get("clipId")
            sec = f"clip {i}"
            if not clip_id:
                rep.add(sec, "fetch", False, "no clipId in event", "clipId")
                continue
            try:
                r = await client.get(f"{c2}/internal/clips/{clip_id}", headers=svc)
            except httpx.HTTPError as exc:
                rep.add(sec, "HTTP 200", False, repr(exc), "200")
                continue
            ctype = r.headers.get("content-type", "")
            rep.add(sec, "HTTP 200", r.status_code == 200, str(r.status_code) + ("" if r.status_code == 200 else f" {r.text[:120]}"), "200")
            if r.status_code != 200:
                continue
            rep.add(sec, "content-type audio/wav", ctype.split(";")[0].strip() == "audio/wav", ctype, "audio/wav")
            path = clip_dir / f"{clip_id}.wav"
            path.write_bytes(r.content)
            try:
                w = check_wav(r.content)
            except (wave.Error, EOFError) as exc:
                rep.add(sec, "WAV parses", False, str(exc), "a valid WAV")
                continue
            rep.add(sec, "mono", w["channels"] == 1, str(w["channels"]), "1 channel")
            rep.add(sec, "16000 Hz", w["rate"] == 16000, str(w["rate"]), "16000")
            rep.add(sec, "16-bit", w["width"] == 2, f"{w['width'] * 8}-bit", "16-bit")
            rep.add(sec, "5.0 s +/- 0.1", abs(w["seconds"] - 5.0) <= 0.1, f"{w['seconds']:.3f} s", "4.9..5.1 s")
            parts = topic.split("/")
            hdr_t, hdr_v = r.headers.get("x-tenant-id"), r.headers.get("x-vehicle-id")
            hdr_ok = hdr_t == parts[2] and hdr_v == parts[3]
            if not hdr_ok:
                rep.notes.append(f"clip {i}: X-Tenant-Id/X-Vehicle-Id {hdr_t}/{hdr_v} differ from the topic {parts[2]}/{parts[3]}")
            clip_rows.append([i, clip_id, r.status_code, ctype, w["channels"], w["rate"], w["width"] * 8,
                              f"{w['seconds']:.3f}", f"{hdr_t}/{hdr_v}", r.headers.get("x-captured-at", "-"), path.name])
        if clip_rows:
            print(table(["#", "clipId", "status", "type", "ch", "Hz", "bits", "sec", "X-Tenant/Vehicle", "X-Captured-At", "file"],
                        clip_rows))

        first_clip = next(((ev.get("clip") or {}).get("clipId") for _, ev in events if (ev.get("clip") or {}).get("clipId")), None)
        probe = first_clip or "nonexistent"
        r = await client.get(f"{c2}/internal/clips/{probe}")
        rep.add("clip auth", "no token -> 401", r.status_code == 401, str(r.status_code), "401")
        user_token = await login(client, web, USERS["tA"])
        if user_token:
            r = await client.get(f"{c2}/internal/clips/{probe}", headers={"Authorization": f"Bearer {user_token}"})
            rep.add("clip auth", "user token (OWNER) -> 403", r.status_code == 403, str(r.status_code), "403")
        else:
            rep.add("clip auth", "user token (OWNER) -> 403", False, "login failed", "403")
        unknown = f"no-such-clip-{uuid.uuid4().hex[:8]}"
        r = await client.get(f"{c2}/internal/clips/{unknown}", headers=svc)
        rep.add("clip auth", "unknown clipId -> 404", r.status_code == 404, str(r.status_code), "404")

        # 3 --------------------------------------------------------------------------------------------
        print(f"\n== 3. MongoDB audio_events ({args.mongo_url})")
        await asyncio.sleep(3)
        db = AsyncIOMotorClient(args.mongo_url, serverSelectionTimeoutMS=5000)[database_name(args.mongo_url)]
        stored: Dict[int, Optional[Dict[str, Any]]] = {}
        mongo_rows = []
        for i, (topic, ev) in enumerate(events, 1):
            tenant = topic.split("/")[2]
            q = {"tenantId": tenant, "$or": [{"eventId": ev.get("eventId")},
                                             {"clip.clipId": (ev.get("clip") or {}).get("clipId")}]}
            doc = await db["audio_events"].find_one(q)
            stored[i] = doc
            mongo_rows.append([i, ev.get("eventId"), "yes" if doc else "no",
                               ", ".join(sorted(doc.keys())) if doc else "-",
                               ("yes" if "inference" in doc else "no") if doc else "-"])
            rep.rows.append((f"mongo {i}", "C2 stored the event in audio_events", "yes" if doc else "no",
                             str(doc["_id"]) if doc else "not found"))
        print(table(["#", "eventId", "stored", "fields", "inference key"], mongo_rows))
        if events and not any(stored.values()):
            rep.notes.append("C2 does not store audio events in MongoDB audio_events: the C4 worker has to create the "
                             "document (C2 only reads audio_events.inference.topK for the Data tab)")

        # 4 --------------------------------------------------------------------------------------------
        print(f"\n== 4. POST {c3}/internal/events/{{eventRef}}/result")
        if not events:
            rep.add("c3", "post result", None, "no captured event")
        else:
            idx = next((i for i, (_, ev) in enumerate(events, 1) if (ev.get("edge") or {}).get("priority") == "HIGH"), 1)
            topic, ev = events[idx - 1]
            tenant, vehicle = topic.split("/")[2], topic.split("/")[3]
            edge = ev.get("edge") or {}
            top_k = [{"class": edge.get("class"), "conf": edge.get("score")}]
            print(f"  event {idx} ({tenant}/{vehicle}, edge {edge.get('class')} {edge.get('score')} {edge.get('priority')})")
            doc = stored.get(idx)
            created = False
            if doc is None:
                ok_ts, _ = parse_iso_utc(ev.get("ts"))
                ts = datetime.fromisoformat(ev["ts"].replace("Z", "+00:00")) if ok_ts else datetime.now(timezone.utc)
                doc = {k: v for k, v in ev.items() if k not in ("ts",)}
                doc.update({"tenantId": tenant, "vehicleId": vehicle, "ts": ts, "expireAt": ts + timedelta(days=90),
                            "inference": {"topK": top_k, "modelVersion": "edge-passthrough", "latencyMs": 0}})
                res = await db["audio_events"].insert_one(doc)
                doc["_id"] = res.inserted_id
                created = True
                print(f"  C2 did not store it: created audio_events {doc['_id']}")
            event_ref = str(doc["_id"])
            body = {"tenantId": tenant, "vehicleId": vehicle, "topK": top_k, "modelVersion": "edge-passthrough",
                    "latencyMs": 0}
            r = await client.post(f"{c3}/internal/events/{event_ref}/result", json=body, headers=svc)
            try:
                rbody = r.json()
            except ValueError:
                rbody = {"raw": r.text[:300]}
            decision = rbody.get("decision", rbody) if isinstance(rbody, dict) else rbody
            print(f"  -> HTTP {r.status_code} {json.dumps(decision)}")
            rep.add("c3", f"POST result (eventRef {event_ref}{', doc created by check' if created else ''})",
                    r.status_code == 200, f"{r.status_code} {short(decision, 80)}", "200 with a decision")
            alert_id = decision.get("alertId") if isinstance(decision, dict) else None
            if alert_id and decision.get("alert", True):
                email = USERS.get(tenant)
                token = await login(client, web, email) if email else None
                if not token:
                    rep.add("c3", f"alert {alert_id} in GET /c3/api/v1/alerts", False, f"no login for {tenant}", "listed")
                else:
                    r = await client.get(f"{web}/c3/api/v1/alerts", params={"size": 100, "sort": "createdAt"},
                                         headers={"Authorization": f"Bearer {token}"})
                    items = (r.json() or {}).get("items", []) if r.status_code == 200 else []
                    hit = next((a for a in items if a.get("alertId") == alert_id), None)
                    rep.add("c3", f"alert {alert_id} in GET /c3/api/v1/alerts as {email}", hit is not None,
                            f"{r.status_code}, {len(items)} items" + (f", {hit.get('type')}/{hit.get('severity')}/{hit.get('status')} "
                                                                       f"eventRef={hit.get('eventRef')}" if hit else ""),
                            "the new alert in the list")
                    if hit and hit.get("eventRef") != event_ref:
                        rep.notes.append(f"alert {alert_id} eventRef {hit.get('eventRef')} != {event_ref}")
            else:
                reason = decision.get("reason") if isinstance(decision, dict) else None
                rep.add("c3", "alert visible through nginx", None, f"no alert: {reason or short(decision)}")

    # 5 ------------------------------------------------------------------------------------------------
    if mq_user:
        rep.notes.append(f"MQTT login from .env is {mq_user}; the spec (Part II 2.8) gives the worker its own svc-worker login, "
                         "which does not exist; the course broker allows anonymous clients, so no login is enforced")
    print("\n== Summary")
    print(table(["section", "check", "result", "detail"], [list(r) for r in rep.rows]))
    counts = {k: sum(1 for r in rep.rows if r[2] == k) for k in (PASS, FAIL, NA)}
    print(f"\n{counts[PASS]} PASS, {counts[FAIL]} FAIL, {counts[NA]} N/A")
    print("\n== Mismatches against the contract")
    print("\n".join(f"  - {m}" for m in rep.mismatches) or "  none")
    print("\n== Notes")
    print("\n".join(f"  - {n}" for n in dict.fromkeys(rep.notes)) or "  none")
    return 1 if rep.failed() else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
