"""Topic parsing, clip fetch (retries, 404, bad bytes), resampling, the Mongo document, the result call."""
import json
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import numpy as np
import pytest

import fakes
from c4_worker.audio import ClipError, decode_wav, resample_to_48k
from c4_worker.clients import fetch_clip
from c4_worker.config import CLASSES, MODEL_ACTIVATED_TOPIC
from c4_worker.model_manager import LoadedModel
from c4_worker.mqtt_in import MqttIn, parse_topic
from c4_worker.prio_queue import PriorityEventQueue, QueuedEvent
from c4_worker.classifiers import HeadClassifier


def load_head(manager, version="1.3.0"):
    manager.current = LoadedModel(1, version, f"sound-classifier-{version}", "ab" * 32, "2026-11-09T18:00:00Z",
                                  HeadClassifier(fakes.head_state_dict(), 512))
    manager.ready.set()


def item(ev, tenant="tA", vehicle="AV-01", mid=11):
    return QueuedEvent.build(tenant, vehicle, ev, mid)


# ---------------------------------------------------------------------------------------- topic / MQTT
def test_parse_topic():
    assert parse_topic("scicp/v1/tA/AV-01/audio/event") == ("tA", "AV-01")
    assert parse_topic("scicp/v1/tB/AV-12/audio/event") == ("tB", "AV-12")
    for bad in ["scicp/v1/tA/audio/event", "scicp/v2/tA/AV-01/audio/event", "scicp/v1/tA/AV-01/telemetry",
                "scicp/v1//AV-01/audio/event", "scicp/v1/tA/AV-01/audio/event/x", MODEL_ACTIVATED_TOPIC]:
        assert parse_topic(bad) is None, bad


def test_mqtt_handle_takes_tenant_and_vehicle_from_topic():
    q = PriorityEventQueue(10)
    woke = []
    m = MqttIn(fakes.settings(), q, woke.append)
    payload = json.dumps(fakes.event(tenant="tZ", vehicle="AV-99")).encode()   # payload disagrees with topic
    got = m.handle("scicp/v1/tB/AV-12/audio/event", payload, mid=5)
    assert (got.tenant_id, got.vehicle_id, got.mid, got.high) == ("tB", "AV-12", 5, True)
    assert len(q) == 1
    assert m.handle("scicp/v1/tB/AV-12/audio/event", b"not json", mid=6) is None
    assert m.handle("scicp/v1/tB/audio/event", payload, mid=7) is None
    assert m.malformed == 2 and len(q) == 1
    m.handle(MODEL_ACTIVATED_TOPIC, b'{"modelId": 1, "version": "1.3.0"}', mid=8, retain=True)
    assert woke == [f"MQTT {MODEL_ACTIVATED_TOPIC} 1.3.0 (retained)"] and len(q) == 1


# ------------------------------------------------------------------------------------------- clip fetch
async def test_fetch_clip_sends_service_token(services, http, token, sleep):
    services.clips["c1"] = b"RIFF..."
    assert await fetch_clip(http, fakes.C2, token, "c1", sleep=sleep) == b"RIFF..."
    auth = services.requests[0].headers["authorization"].split()[1]
    claims = jwt.decode(auth, fakes.SECRET, algorithms=["HS256"])
    assert claims["role"] == "SERVICE" and claims["sub"] == "c4-worker" and claims["jti"]


async def test_fetch_clip_retries_5xx_and_timeouts_then_succeeds(services, http, token, sleep, waits):
    services.clips["c1"] = b"ok"
    services.clip_failures["c1"] = [503, httpx.ReadTimeout("slow")]
    assert await fetch_clip(http, fakes.C2, token, "c1", retries=2, sleep=sleep) == b"ok"
    assert len(services.requests) == 3 and waits == [1.0, 2.0]


async def test_fetch_clip_gives_up_after_two_retries(services, http, token, sleep):
    services.clip_failures["c1"] = [httpx.ConnectTimeout("x")] * 3
    with pytest.raises(ClipError, match="after 3 attempts"):
        await fetch_clip(http, fakes.C2, token, "c1", retries=2, sleep=sleep)
    assert len(services.requests) == 3


async def test_fetch_clip_404_is_not_retried(services, http, token, sleep):
    with pytest.raises(ClipError, match="not found"):
        await fetch_clip(http, fakes.C2, token, "missing", sleep=sleep)
    assert len(services.requests) == 1


# -------------------------------------------------------------------------------------- decode / resample
def test_decode_and_resample_to_48k():
    samples = decode_wav(fakes.wav_bytes())
    assert samples.dtype == np.float32 and len(samples) == 80000 and np.abs(samples).max() <= 1.0
    up = resample_to_48k(samples)
    assert up.dtype == np.float32 and len(up) == 240000
    # the 440 Hz tone survives: dominant FFT bin at 440 Hz
    spectrum = np.abs(np.fft.rfft(up))
    assert abs(np.argmax(spectrum) * 48000 / len(up) - 440) < 2


@pytest.mark.parametrize("data,match", [(b"garbage bytes", "undecodable"), (b"", "undecodable"),
                                        (fakes.wav_bytes(1, rate=44100), "44100 Hz"),
                                        (fakes.wav_bytes(1, width=1), "8-bit")])
def test_decode_rejects(data, match):
    with pytest.raises(ClipError, match=match):
        decode_wav(data)


# ----------------------------------------------------------------------------------- the whole event
async def test_process_stores_document_then_acks_then_posts_result(pipeline, manager, services, db, encoder, acks,
                                                                  monkeypatch):
    import c4_worker.pipeline as pl
    real_store = pl.store_event

    async def traced_store(database, doc):
        out = await real_store(database, doc)
        services.log.append("stored")
        return out
    monkeypatch.setattr(pl, "store_event", traced_store)
    load_head(manager)
    ev = fakes.event(clip_id="c1")
    services.clips["c1"] = fakes.wav_bytes()
    out = await pipeline.process(item(ev, mid=42))

    doc = await db["audio_events"].find_one({"eventId": ev["eventId"]})
    assert str(doc["_id"]) == out["eventRef"]
    ts = datetime(2026, 11, 9, 18, 30, 5, tzinfo=timezone.utc)
    assert doc["ts"].replace(tzinfo=timezone.utc) == ts
    assert doc["expireAt"].replace(tzinfo=timezone.utc) == ts + timedelta(days=90)
    assert {k: doc[k] for k in ("tenantId", "vehicleId", "deviceId", "channel")} == \
        {"tenantId": "tA", "vehicleId": "AV-01", "deviceId": "dev-0112", "channel": "exterior"}
    assert doc["clip"] == ev["clip"] and doc["edge"] == ev["edge"] and doc["context"] == ev["context"]
    assert doc["location"] == ev["location"]
    inf = doc["inference"]
    assert set(inf) == {"topK", "modelVersion", "latencyMs"}
    assert inf["modelVersion"] == "sound-classifier-1.3.0" and isinstance(inf["latencyMs"], int)
    assert [t["class"] for t in inf["topK"]][0] == "crash" and len(inf["topK"]) == 3
    assert set(doc) == {"_id", "eventId", "tenantId", "vehicleId", "deviceId", "ts", "channel", "location", "clip",
                        "edge", "inference", "context", "expireAt"}

    assert encoder.audio_calls == [240000]            # resampled to 48 kHz before the encoder
    assert acks == [42]
    assert services.log == ["clip c1", "stored", "ack 42", f"result {out['eventRef']}"]
    body = services.results[0]["body"]
    assert set(body) == {"tenantId", "vehicleId", "topK", "modelVersion", "latencyMs"}
    assert body["topK"] == inf["topK"] and body["modelVersion"] == "sound-classifier-1.3.0"
    assert body["latencyMs"] == inf["latencyMs"]
    assert out["decision"]["reason"] == "ALERT_CREATED"
    assert pipeline.processed == 1 and pipeline.errors == 0


async def test_topic_tenant_wins_over_payload(pipeline, manager, services, db):
    load_head(manager)
    services.clips["c1"] = fakes.wav_bytes()
    await pipeline.process(item(fakes.event(clip_id="c1", tenant="tZ", vehicle="AV-99"), "tB", "AV-12"))
    doc = await db["audio_events"].find_one({})
    assert (doc["tenantId"], doc["vehicleId"]) == ("tB", "AV-12")
    assert services.results[0]["body"]["tenantId"] == "tB" and services.results[0]["body"]["vehicleId"] == "AV-12"


async def test_duplicate_event_id_reuses_the_stored_id(pipeline, manager, services, db, acks):
    load_head(manager)
    services.clips["c1"] = fakes.wav_bytes()
    first = await pipeline.process(item(fakes.event(clip_id="c1"), mid=1))
    again = await pipeline.process(item(fakes.event(clip_id="c1"), mid=2))   # MQTT redelivery
    assert first["inserted"] and not again["inserted"]
    assert again["eventRef"] == first["eventRef"]
    assert await db["audio_events"].count_documents({}) == 1
    assert [r["eventRef"] for r in services.results] == [first["eventRef"]] * 2
    assert acks == [1, 2]


async def test_same_event_id_in_another_tenant_is_a_new_document(pipeline, manager, services, db):
    load_head(manager)
    services.clips["c1"] = fakes.wav_bytes()
    a = await pipeline.process(item(fakes.event(clip_id="c1"), "tA", "AV-01"))
    b = await pipeline.process(item(fakes.event(clip_id="c1"), "tB", "AV-10"))
    assert a["eventRef"] != b["eventRef"] and await db["audio_events"].count_documents({}) == 2


async def test_duplicate_without_inference_gets_one(pipeline, manager, services, db):
    load_head(manager)
    ev = fakes.event(clip_id="c1")
    await db["audio_events"].insert_one({"eventId": ev["eventId"], "tenantId": "tA", "vehicleId": "AV-01"})
    services.clips["c1"] = fakes.wav_bytes()
    out = await pipeline.process(item(ev))
    doc = await db["audio_events"].find_one({})
    assert not out["inserted"] and doc["inference"]["modelVersion"] == "sound-classifier-1.3.0"


@pytest.mark.parametrize("setup,match", [
    (lambda s: None, "not found"),                                       # C2 404
    (lambda s: s.clips.__setitem__("c1", b"\x00not a wav"), "undecodable"),
    (lambda s: s.clip_failures.__setitem__("c1", [500, 502, 503]), "after 3 attempts"),
])
async def test_clip_error_stores_inference_error_and_posts_error(pipeline, manager, services, db, setup, match, acks):
    load_head(manager)
    setup(services)
    out = await pipeline.process(item(fakes.event(clip_id="c1"), mid=9))
    doc = await db["audio_events"].find_one({})
    assert match in doc["inference"]["error"] and doc["inference"]["topK"] == []
    assert doc["inference"]["modelVersion"] == "sound-classifier-1.3.0"
    body = services.results[0]["body"]
    assert set(body) == {"tenantId", "vehicleId", "error", "modelVersion"}
    assert body["tenantId"] == "tA" and match in body["error"] and body["modelVersion"] == "sound-classifier-1.3.0"
    assert out["decision"]["reason"] == "ERROR_RECORDED"
    assert acks == [9] and pipeline.errors == 1


async def test_result_retries_after_1_2_4_seconds(pipeline, manager, services, waits):
    load_head(manager)
    services.clips["c1"] = fakes.wav_bytes()
    services.result_failures = [503, httpx.ConnectError("down"), 502]
    out = await pipeline.process(item(fakes.event(clip_id="c1")))
    assert waits == [1.0, 2.0, 4.0] and out["decision"]["reason"] == "ALERT_CREATED"
    assert sum(1 for r in services.requests if r.url.path.endswith("/result")) == 4


async def test_result_gives_up_after_four_calls(pipeline, manager, services, waits, db):
    load_head(manager)
    services.clips["c1"] = fakes.wav_bytes()
    services.result_failures = [503] * 5
    out = await pipeline.process(item(fakes.event(clip_id="c1")))
    assert out["decision"] is None and waits == [1.0, 2.0, 4.0] and pipeline.errors == 1
    assert sum(1 for r in services.requests if r.url.path.endswith("/result")) == 4
    assert await db["audio_events"].count_documents({}) == 1      # stored all the same


async def test_result_4xx_is_not_retried(pipeline, manager, services, waits):
    load_head(manager)
    services.clips["c1"] = fakes.wav_bytes()
    services.result_failures = [422]
    out = await pipeline.process(item(fakes.event(clip_id="c1")))
    assert out["decision"] is None and waits == []


async def test_event_without_event_id_is_acked_and_counted(pipeline, manager, services, db, acks):
    import asyncio
    load_head(manager)
    q = PriorityEventQueue(5)
    q.put(item({"clip": {"clipId": "c1"}}, mid=3))
    task = asyncio.create_task(pipeline.run(q))
    for _ in range(50):
        if acks:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    assert acks == [3] and pipeline.errors == 1 and await db["audio_events"].count_documents({}) == 0


def test_classes_order():
    assert CLASSES == ("siren", "crash", "glass_break", "scream", "honking", "traffic", "environmental")
