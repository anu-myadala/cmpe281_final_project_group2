"""Loading both artifact kinds, bad artifacts keep the old model, swaps happen between clips, NO_MODEL waits."""
import asyncio

import pytest
import torch

import fakes
from c4_worker.classifiers import ArtifactError, HeadClassifier, ZeroShotClassifier, load_artifact
from c4_worker.config import CLASSES
from c4_worker.prio_queue import PriorityEventQueue, QueuedEvent


# ---------------------------------------------------------------------------------------- artifacts
def test_load_head(tmp_path, encoder):
    clf = load_artifact(fakes.write_head(tmp_path / "m.pt"), encoder)
    assert isinstance(clf, HeadClassifier) and clf.kind == "head"
    top = clf.classify(fakes.basis("glass_break") * 4)
    assert top[0][0] == "glass_break" and len(top) == 3 and top[0][1] > 0.9
    assert all(c in CLASSES for c, _ in top) and top[0][1] >= top[1][1] >= top[2][1]


def test_load_head_with_prefixed_state_dict(tmp_path, encoder):
    sd = {f"net.{k}": v for k, v in fakes.head_state_dict().items()}
    clf = load_artifact(fakes.write_head(tmp_path / "m.pt", state_dict=sd), encoder)
    assert clf.classify(fakes.basis("siren"))[0][0] == "siren"


def test_load_zero_shot_embeds_prompts_once(tmp_path):
    enc = fakes.FakeEncoder()
    clf = load_artifact(fakes.write_zero_shot(tmp_path / "m.pt"), enc)
    assert isinstance(clf, ZeroShotClassifier) and len(enc.text_calls) == 7
    assert enc.text_calls[0] == ["a siren sound", "the noise of siren"]
    top = clf.classify(fakes.basis("scream") * 5)
    assert top[0][0] == "scream" and top[0][1] > 0.9 and len(top) == 3
    assert len(enc.text_calls) == 7                     # nothing re-embedded per clip


def test_zero_shot_temperature_softens(tmp_path):
    enc = fakes.FakeEncoder()
    sharp = load_artifact(fakes.write_zero_shot(tmp_path / "a.pt", temperature=0.01), enc)
    soft = load_artifact(fakes.write_zero_shot(tmp_path / "b.pt", temperature=1.0), enc)
    x = fakes.basis("honking")
    assert sharp.classify(x)[0][1] > soft.classify(x)[0][1]


def test_real_zero_shot_tool_output_loads(tmp_path, monkeypatch):
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location("mzs", fakes.Path(__file__).parents[1] / "make_zero_shot_model.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(sys, "argv", ["make_zero_shot_model.py", "--out", str(tmp_path)])
    assert mod.main() == 0
    import hashlib, json
    data = (tmp_path / "model.pt").read_bytes()
    assert torch.load(tmp_path / "model.pt", weights_only=True)["temperature"] == 0.05
    monkeypatch.setattr(sys, "argv", ["make_zero_shot_model.py", "--version", "9.9.9", "--temperature", "0.07"])
    monkeypatch.setenv("SCICP_DATA", str(tmp_path / "data"))
    assert mod.main() == 0
    out = tmp_path / "data" / "work" / "models" / "zero_shot_9.9.9"
    assert torch.load(out / "model.pt", weights_only=True)["temperature"] == 0.07
    assert "9.9.9" in json.loads((out / "metrics.json").read_text())["note"]
    assert (tmp_path / "model.pt.sha256").read_text().split()[0] == hashlib.sha256(data).hexdigest()
    metrics = json.loads((tmp_path / "metrics.json").read_text())
    assert set(metrics) >= {"macroF1", "accuracy", "perClass"} and list(metrics["perClass"]) == list(CLASSES)
    assert set(metrics["perClass"]["siren"]) == {"precision", "recall", "f1", "support"}

    class TextEncoder(fakes.FakeEncoder):   # map the real prompts to their class for the fake
        def embed_text(self, texts):
            cls = next(c for c, p in mod.PROMPTS.items() if p == list(texts))
            return torch.stack([fakes.basis(cls)] * len(texts))
    clf = load_artifact(tmp_path / "model.pt", TextEncoder())
    assert clf.classify(fakes.basis("traffic"))[0][0] == "traffic"


def _zero_shot_with_text(text_rows, temperature=None):
    """A ZeroShotClassifier whose class text embeddings are exactly `text_rows` (7 x 512)."""
    class RowsEncoder(fakes.FakeEncoder):
        def embed_text(self, texts):
            cls = next(c for c in CLASSES if c in texts[0])
            return text_rows[CLASSES.index(cls)].reshape(1, -1).repeat(len(texts), 1)
    prompts = {c: [f"a {c} sound"] for c in CLASSES}
    kw = {} if temperature is None else {"temperature": temperature}
    return ZeroShotClassifier(prompts, RowsEncoder(), **kw)


def test_zero_shot_equal_similarities_are_uniform():
    same = torch.stack([fakes.basis("siren")] * 7)            # every class has the same text embedding
    clf = _zero_shot_with_text(same)
    top = clf.classify(fakes.basis("siren") + fakes.basis("crash"))
    assert [p for _, p in top] == [round(1 / 7, 4)] * 3


def test_zero_shot_big_gap_is_not_exactly_one():
    # cos 0.5 with one class and 0.0 with the six others: a wider gap and range than any C2 clip
    # gave in the temperature sweep (largest gap 0.137, largest range 0.478)
    text = torch.stack([fakes.basis(c) for c in CLASSES])
    audio = 0.5 * fakes.basis("crash")
    audio[100] = 0.75 ** 0.5                                    # unit vector, cosine 0.5 with crash only
    top = _zero_shot_with_text(text).classify(audio)
    assert top[0][0] == "crash" and 0.99 < top[0][1] < 1.0      # default T 0.05: 0.9997 after rounding
    assert _zero_shot_with_text(text, temperature=0.01).classify(audio)[0][1] == 1.0   # the 1.3.0 problem


def test_default_temperature_is_shared_by_tool_and_classifier(tmp_path, encoder):
    import importlib.util
    from c4_worker.classifiers import DEFAULT_ZERO_SHOT_TEMPERATURE
    spec = importlib.util.spec_from_file_location("mzs", fakes.Path(__file__).parents[1] / "make_zero_shot_model.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.DEFAULT_TEMPERATURE == DEFAULT_ZERO_SHOT_TEMPERATURE == 0.05
    path = tmp_path / "old.pt"
    torch.save({"kind": "zero_shot", "classes": list(CLASSES), "prompts": {c: [f"a {c}"] for c in CLASSES}}, path)
    assert load_artifact(path, encoder).temperature == 0.05     # artifact without 'temperature'
    assert load_artifact(fakes.write_zero_shot(tmp_path / "t.pt", temperature=0.01), encoder).temperature == 0.01


@pytest.mark.parametrize("make,match", [
    (lambda p: p.write_bytes(b"not a torch file"), "unreadable"),
    (lambda p: torch.save([1, 2, 3], p), "'kind'"),
    (lambda p: torch.save({"kind": "svm", "classes": list(CLASSES)}, p), "unknown kind"),
    (lambda p: fakes.write_head(p, classes=list(reversed(CLASSES))), "classes"),
    (lambda p: fakes.write_head(p, classes=list(CLASSES[:6])), "classes"),
    (lambda p: fakes.write_head(p, embedding_dim=768), "embedding_dim"),
    (lambda p: fakes.write_head(p, state_dict={"0.weight": torch.zeros(3, 3)}), "head"),
    (lambda p: fakes.write_zero_shot(p, prompts={"siren": ["a siren"]}), "no prompts for crash"),
])
def test_bad_artifacts(tmp_path, encoder, make, match):
    path = tmp_path / "m.pt"
    make(path)
    with pytest.raises(ArtifactError, match=match):
        load_artifact(path, encoder)


# ------------------------------------------------------------------------------------- ModelManager
def head_bytes(tmp_path, name="h.pt"):
    return fakes.write_head(tmp_path / name).read_bytes()


async def test_sync_loads_head_and_reports_loading_then_ready(manager, services, tmp_path):
    services.set_active(head_bytes(tmp_path), "1.3.0")
    assert manager.state == "NO_MODEL" and not manager.ready.is_set()
    assert await manager.sync("startup")
    assert services.states() == ["LOADING", "READY"]
    assert manager.state == "READY" and manager.model_version == "sound-classifier-1.3.0" and manager.ready.is_set()
    assert manager.current.classifier.kind == "head"


async def test_sync_loads_zero_shot(manager, services, tmp_path):
    services.set_active(fakes.write_zero_shot(tmp_path / "z.pt").read_bytes(), "2.0.0")
    await manager.sync("startup")
    assert manager.current.classifier.kind == "zero_shot" and services.states() == ["LOADING", "READY"]
    assert "zero_shot" in services.status_reports[-1]["detail"]


async def test_hash_mismatch_keeps_old_model_and_reports_failed(manager, services, tmp_path):
    services.set_active(head_bytes(tmp_path), "1.3.0")
    await manager.sync("startup")
    old = manager.current
    services.set_active(fakes.write_zero_shot(tmp_path / "z.pt").read_bytes(), "1.4.0", sha256="0" * 64)
    await manager.sync("model.activated")
    assert manager.current is old and manager.model_version == "sound-classifier-1.3.0"
    assert services.states() == ["LOADING", "READY", "LOADING", "FAILED"]
    failed = services.status_reports[-1]
    assert failed["modelVersion"] == "1.4.0" and failed["sha256"] is None
    assert "sha256 mismatch" in failed["detail"] and "still serving sound-classifier-1.3.0" in failed["detail"]
    assert not list((tmp_path / "cache").glob("*1.4.0*"))     # the bad download is deleted


async def test_unreadable_artifact_with_right_hash_reports_failed(manager, services, tmp_path):
    services.set_active(head_bytes(tmp_path), "1.3.0")
    await manager.sync("startup")
    old = manager.current
    services.set_active(b"PK\x03\x04 definitely not torch", "1.4.0")
    await manager.sync("poll")
    assert manager.current is old and services.states()[-1] == "FAILED"
    assert "load failed: unreadable artifact" in services.status_reports[-1]["detail"]


async def test_wrong_format_with_no_previous_model(manager, services, tmp_path):
    path = tmp_path / "bad.pt"
    torch.save({"kind": "head", "classes": ["a", "b"]}, path)
    services.set_active(path.read_bytes(), "1.0.0")
    await manager.sync("startup")
    assert manager.current is None and manager.state == "NO_MODEL" and not manager.ready.is_set()
    assert services.states() == ["LOADING", "FAILED"] and "still serving" not in services.status_reports[-1]["detail"]


async def test_new_version_swapped_between_clips_only(manager, pipeline, services, tmp_path):
    services.set_active(head_bytes(tmp_path), "1.3.0")
    await manager.sync("startup")
    services.clips["c1"] = fakes.wav_bytes()
    services.clips["c2"] = fakes.wav_bytes()
    in_clip, release = asyncio.Event(), asyncio.Event()

    async def slow_clip(clip_id):
        if clip_id == "c1":
            in_clip.set()
            await release.wait()
    services.on_clip = slow_clip

    first = asyncio.create_task(pipeline.process(QueuedEvent.build("tA", "AV-01", fakes.event(clip_id="c1"))))
    await in_clip.wait()                                   # clip 1 is being processed with 1.3.0
    services.set_active(fakes.write_zero_shot(tmp_path / "z.pt").read_bytes(), "1.4.0")
    sync = asyncio.create_task(manager.sync("model.activated"))
    for _ in range(100):                                   # let the download and the load finish
        await asyncio.sleep(0.01)
        if services.states()[-1] == "LOADING" and sync.done() is False and len(services.requests) > 4:
            break
    await asyncio.sleep(0.1)
    assert manager.model_version == "sound-classifier-1.3.0"      # not swapped mid-clip
    assert services.states() == ["LOADING", "READY", "LOADING"]
    release.set()
    out1 = await first
    await sync
    assert out1["body"]["modelVersion"] == "sound-classifier-1.3.0"
    assert manager.model_version == "sound-classifier-1.4.0" and services.states()[-1] == "READY"
    assert "(was sound-classifier-1.3.0)" in services.status_reports[-1]["detail"]
    out2 = await pipeline.process(QueuedEvent.build("tA", "AV-01", fakes.event(event_id="e2", clip_id="c2")))
    assert out2["body"]["modelVersion"] == "sound-classifier-1.4.0"


async def test_no_model_processes_nothing_and_events_wait(manager, pipeline, services, tmp_path, db):
    queue = PriorityEventQueue(10)
    for n in range(3):
        services.clips[f"c{n}"] = fakes.wav_bytes()
        queue.put(QueuedEvent.build("tA", "AV-01", fakes.event(event_id=f"e{n}", clip_id=f"c{n}"), mid=n))
    assert not await manager.sync("startup")               # C1: 404, nothing ACTIVE
    assert manager.state == "NO_MODEL" and services.status_reports == []
    runner = asyncio.create_task(pipeline.run(queue))
    await asyncio.sleep(0.2)
    assert len(queue) == 3 and pipeline.processed == 0
    assert not any("/internal/clips/" in str(r.url) for r in services.requests)
    assert await db["audio_events"].count_documents({}) == 0

    services.set_active(head_bytes(tmp_path), "1.3.0")      # the first activation arrives
    await manager.sync("model.activated")
    for _ in range(200):
        if pipeline.processed == 3:
            break
        await asyncio.sleep(0.01)
    runner.cancel()
    assert pipeline.processed == 3 and len(queue) == 0
    assert await db["audio_events"].count_documents({"inference.modelVersion": "sound-classifier-1.3.0"}) == 3
