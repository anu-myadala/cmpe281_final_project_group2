"""The edge agent's control handling, pre-filter and sound bank, and the deploy files (nginx must differ
from C1's file only in the upstream lines, as c3/tests/test_deploy_files.py checks for C3)."""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import numpy as np

C2_DIR = Path(__file__).resolve().parents[1]
REPO = C2_DIR.parent
sys.path.insert(0, str(C2_DIR / "edge"))

from agent import AgentConfig, EdgeAgent, PreFilter  # noqa: E402
from sounds import CLASSES, RATE, SoundBank, synthetic, to_wav  # noqa: E402

CFG = {"tenantId": "tA", "vehicleId": "AV-02",
       "mqtt": {"host": "localhost", "port": 1883, "username": "tA-AV-02", "password": "x", "topicRoot": "scicp/v1/tA/AV-02"},
       "api": {"baseUrl": "http://localhost:8080/c2/api/v1", "ingestToken": "t"},
       "devices": [{"deviceId": "dev-0210", "type": "EDGE_GATEWAY"}, {"deviceId": "dev-0211", "type": "MIC_INTERIOR"},
                   {"deviceId": "dev-0212", "type": "MIC_EXTERIOR"}], "micGainDb": 6, "detectionThreshold": 0.4}


class Recorder(EdgeAgent):
    def __init__(self):
        super().__init__(AgentConfig(None, json.loads(json.dumps(CFG))))
        self.published = []

    def _publish(self, kind, payload, qos=1, retain=False):
        self.published.append((kind, payload))


def test_agent_applies_and_acks_settings():
    a = Recorder()
    a.handle_control({"commandId": 7, "type": "SET_CONFIG", "deviceId": "dev-0212", "version": 4,
                      "settings": {"micGainDb": 12, "detectionThreshold": 0.5}})
    kind, ack = a.published[-1]
    assert kind == "status" and ack["result"] == "APPLIED" and ack["reported"]["micGainDb"] == 12
    assert a.cfg.mic_settings("dev-0212")["version"] == 4
    a.handle_control({"commandId": 8, "type": "SET_CONFIG", "deviceId": "dev-0212", "settings": {"micGainDb": 40}})
    assert a.published[-1][1]["result"] == "REJECTED"
    a.handle_control({"commandId": 9, "type": "MUTE", "deviceId": "dev-0211"})
    assert a.cfg.mic_settings("dev-0211")["muted"] is True
    assert a.send_clip("dev-0211", np.zeros(RATE * 5, dtype=np.float32)) is None   # a muted mic sends nothing
    a.handle_control({"commandId": 10, "type": "SET_NETWORK", "profiles": [{"priority": 1, "ssid": "Depot",
                                                                            "securityType": "WPA2_PSK", "password": "secret-pass"}]})
    assert a.published[-1][1]["result"] == "APPLIED" and "secret-pass" not in json.dumps(a.cfg.data)
    a.handle_control({"commandId": 11, "type": "SET_CONFIG", "deviceId": "dev-9999", "settings": {}})
    assert a.published[-1][1]["result"] == "REJECTED"


def test_telemetry_lists_the_microphones():
    a = Recorder()
    a.started = 0
    a.faults["dev-0211"] = {"reason": "NO_SIGNAL", "until": 1e12}
    msg = a.telemetry_once()
    assert [d["deviceId"] for d in msg["devices"]] == ["dev-0211", "dev-0212"]
    assert msg["devices"][0]["ok"] is False and msg["devices"][0]["reason"] == "NO_SIGNAL"
    assert msg["location"]["type"] == "Point"


def test_prefilter_fires_on_a_loud_sound_only():
    pf = PreFilter()
    rng = np.random.default_rng(1)
    quiet = lambda: (0.01 * rng.standard_normal(RATE // 10)).astype(np.float32)  # noqa: E731
    t = 0.0
    for _ in range(50):
        t += 0.1
        assert pf.feed(quiet(), 0.40, t) is False
    assert pf.feed((0.5 * rng.standard_normal(RATE // 10)).astype(np.float32), 0.40, t + 0.1) is True
    assert pf.feed((0.5 * rng.standard_normal(RATE // 10)).astype(np.float32), 0.40, t + 0.2) is False  # 5 s hold-off


def test_synthetic_sounds_are_valid_clips():
    rng = random.Random(3)
    for cls in CLASSES:
        s = synthetic(cls, rng)
        assert len(s) == RATE * 5 and np.abs(s).max() <= 1.0
    wav = to_wav(synthetic("siren", rng))
    assert wav[:4] == b"RIFF" and len(wav) == 44 + RATE * 5 * 2
    cls, samples, source = SoundBank(None, rng).pick("crash")
    assert cls == "crash" and source == "synthetic"


def test_nginx_files_change_only_the_upstreams():
    base = (REPO / "c1" / "web" / "nginx" / "default.conf").read_text().splitlines()
    for name, changed in (("nginx-default.conf", {"upstream c2_stub  { server c2:8002; }"}),
                          ("nginx-c2-c3.conf", {"upstream c2_stub  { server c2:8002; }", "upstream c3_stub  { server c3:8003; }"})):
        ours = (C2_DIR / "deploy" / name).read_text().splitlines()
        assert len(ours) == len(base)
        diff = {o for b, o in zip(base, ours) if b != o}
        assert diff == changed, name
