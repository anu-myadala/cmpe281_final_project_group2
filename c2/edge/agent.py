"""SCICP edge agent (module M9 of the C2 design, Section 3.1.2).

One program for both kinds of car:

    live car       python agent.py --config agent.yaml --mic [--state driving|parked]
                   listens to the laptop microphone; the pre-filter cuts a 5 s clip (2 s pre-roll,
                   3 s after) when a sound rises above the background
    simulated car  python agent.py --config agent.yaml --replay datasets/   (or no folder: synthetic)
                   every 20 to 60 s replays a clip of a random class

Either way the agent then does what a car does: POST /ingest/clips with its device token,
publish the audio event on scicp/v1/{tenant}/{vehicle}/audio/event (QoS 1), send telemetry
every 10 s (the heartbeat), keep a retained status with a last will, and apply the settings and
commands C2 sends on /config and /command, answering each with an ack on /status.

agent.yaml is the file the provisioning dialog downloads (Section 5.5). Needs: numpy, paho-mqtt,
httpx, pyyaml; sounddevice for --mic.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import queue
import random
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
import numpy as np
import yaml

try:
    from .sounds import RATE, SoundBank, to_wav
except ImportError:  # run as a script
    from sounds import RATE, SoundBank, to_wav

log = logging.getLogger("scicp.edge")

SJSU = (37.3352, -121.8811)
# Street loops around SJSU (the ones the seed's telemetry uses), so a car keeps driving its own block.
PATHS = {
    "AV-01": [(37.3382, -121.8855), (37.3382, -121.8775), (37.3330, -121.8775), (37.3330, -121.8855)],
    "AV-10": [(37.3366, -121.8905), (37.3366, -121.8855), (37.3340, -121.8855), (37.3340, -121.8905)],
    "AV-11": [(37.3395, -121.8905), (37.3395, -121.8800), (37.3382, -121.8800), (37.3382, -121.8905)],
    "AV-12": [(37.3382, -121.8775), (37.3382, -121.8700), (37.3310, -121.8700), (37.3310, -121.8775)],
    "AV-13": [(37.3330, -121.8855), (37.3330, -121.8775), (37.3290, -121.8775), (37.3290, -121.8855)],
}
GAIN_RANGE, THRESHOLD_RANGE = (-12, 24), (0.10, 0.95)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"


# -- configuration ---------------------------------------------------------------------------------------
class AgentConfig:
    def __init__(self, path: Optional[str], data: Dict[str, Any]) -> None:
        self.path = path
        self.data = data
        self.lock = threading.Lock()

    @classmethod
    def load(cls, path: str) -> "AgentConfig":
        with open(path, "r", encoding="utf-8") as f:
            return cls(path, yaml.safe_load(f))

    def save(self) -> None:
        if not self.path:
            return
        with self.lock, open(self.path, "w", encoding="utf-8") as f:
            f.write("# SCICP edge agent configuration - written by C2 provisioning; keep it secret\n")
            yaml.safe_dump(self.data, f, sort_keys=False)

    tenant = property(lambda self: self.data["tenantId"])
    vehicle = property(lambda self: self.data["vehicleId"])
    topic_root = property(lambda self: self.data["mqtt"]["topicRoot"])

    @property
    def mics(self) -> List[Dict[str, Any]]:
        return [d for d in self.data.get("devices", []) if str(d.get("type", "")).startswith("MIC")]

    def mic_settings(self, device_id: str) -> Dict[str, Any]:
        per = self.data.setdefault("settings", {})
        return per.setdefault(device_id, {"micGainDb": self.data.get("micGainDb", 6),
                                          "detectionThreshold": self.data.get("detectionThreshold", 0.40),
                                          "muted": False, "version": 1})


# -- vehicle context and telemetry -------------------------------------------------------------------------
def _dist_m(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    d = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(d))


class RouteContext:
    """Speed, drive mode, ignition and position: a car driving its block, or parked (--state parked)."""

    def __init__(self, vehicle: str, state: str, rng: random.Random) -> None:
        self.path = PATHS.get(vehicle) or [(SJSU[0] + 0.002, SJSU[1] - 0.002), (SJSU[0] + 0.002, SJSU[1] + 0.002),
                                           (SJSU[0] - 0.002, SJSU[1] + 0.002), (SJSU[0] - 0.002, SJSU[1] - 0.002)]
        self.state = state
        self.rng = rng
        self.progress = rng.uniform(0, 1500)
        self.speed = 0.0 if state == "parked" else rng.uniform(20, 35)
        self.last = time.time()

    def _point(self) -> tuple:
        legs = [(self.path[i], self.path[(i + 1) % len(self.path)]) for i in range(len(self.path))]
        lengths = [_dist_m(a, b) for a, b in legs]
        s = self.progress % sum(lengths)
        for (a, b), length in zip(legs, lengths):
            if s <= length:
                f = s / length if length else 0
                return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
            s -= length
        return self.path[0]

    def step(self) -> Dict[str, Any]:
        now = time.time()
        dt, self.last = now - self.last, now
        if self.state == "parked":
            self.speed = 0.0
        else:
            roll = self.rng.random()
            self.speed = 0.0 if roll < 0.06 else (self.rng.uniform(8, 20) if roll < 0.25
                                                  else min(50.0, max(12.0, self.speed + self.rng.uniform(-6, 6))))
            self.progress += self.speed / 3.6 * dt
        lat, lon = self._point()
        return {"speedKph": round(self.speed, 1), "driveMode": "AUTONOMOUS", "ignition": self.state != "parked",
                "location": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]}}


# -- audio: live microphone with pre-filter and clip cutter --------------------------------------------
class PreFilter:
    """Running noise baseline (median level of the last 30 s); fires when a 0.1 s block is more than
    30 x detectionThreshold dB above it (0.40 -> 12 dB); at most one trigger per 5 s."""

    def __init__(self) -> None:
        self.levels: deque = deque(maxlen=300)
        self.last_trigger = 0.0

    @staticmethod
    def level_db(block: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(np.square(block.astype(np.float64))))) + 1e-9
        return 20 * math.log10(rms) + 94  # rough dB SPL-like scale, only differences matter

    def feed(self, block: np.ndarray, threshold: float, t: float) -> bool:
        level = self.level_db(block)
        baseline = float(np.median(self.levels)) if len(self.levels) >= 20 else None
        self.levels.append(level)
        if baseline is None or t - self.last_trigger < 5.0:
            return False
        if level - baseline > 30 * threshold:
            self.last_trigger = t
            return True
        return False


class MicSource:
    """sounddevice input at 16 kHz mono in 0.1 s blocks, kept in a 10 s ring buffer."""

    def __init__(self, device: Optional[str] = None) -> None:
        import sounddevice as sd  # only the live car needs it
        self.sd = sd
        self.device = device
        self.blocks: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=200)
        self.ring: deque = deque(maxlen=100)

    def start(self) -> None:
        def callback(indata, frames, time_info, status):  # noqa: ARG001
            block = indata[:, 0].copy()
            self.ring.append(block)
            try:
                self.blocks.put_nowait(block)
            except queue.Full:
                pass
        self.stream = self.sd.InputStream(samplerate=RATE, channels=1, blocksize=RATE // 10, dtype="float32",
                                          device=self.device, callback=callback)
        self.stream.start()

    def cut(self, pre_s: float = 2.0, post_s: float = 3.0) -> np.ndarray:
        """Waits post_s after the trigger, then returns the last pre_s + post_s seconds."""
        time.sleep(post_s)
        data = np.concatenate(list(self.ring)) if self.ring else np.zeros(0, dtype=np.float32)
        need = int((pre_s + post_s) * RATE)
        data = data[-need:]
        if len(data) < need:
            data = np.concatenate([np.zeros(need - len(data), dtype=np.float32), data])
        return data


def apply_gain(samples: np.ndarray, gain_db: float) -> np.ndarray:
    return np.clip(samples * (10 ** (gain_db / 20)), -1.0, 1.0)


# -- the agent ------------------------------------------------------------------------------------------
class EdgeAgent:
    def __init__(self, cfg: AgentConfig, state: str = "driving", seed: Optional[int] = None,
                 api_base: Optional[str] = None, mqtt_host: Optional[str] = None, mqtt_port: Optional[int] = None) -> None:
        self.cfg = cfg
        self.rng = random.Random(seed)
        self.context = RouteContext(cfg.vehicle, state, self.rng)
        self.api_base = (api_base or cfg.data["api"]["baseUrl"]).rstrip("/")
        self.mqtt_host = mqtt_host or cfg.data["mqtt"]["host"]
        self.mqtt_port = int(mqtt_port or cfg.data["mqtt"]["port"])
        self.faults: Dict[str, Dict[str, Any]] = {}   # deviceId -> {reason, until}
        self.stop_event = threading.Event()
        self.client = None
        self.http = httpx.Client(timeout=10)
        self.uploads = 0
        self.on_event: Optional[Callable[[dict], None]] = None

    # MQTT ------------------------------------------------------------------------------------------
    def topic(self, kind: str) -> str:
        return f"{self.cfg.topic_root}/{kind}"

    def connect(self) -> None:
        import paho.mqtt.client as mqtt
        m = self.cfg.data["mqtt"]
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=m.get("clientId") or m["username"],
                             clean_session=False)
        client.username_pw_set(m["username"], m.get("password"))
        client.will_set(self.topic("status"), json.dumps({"type": "agent", "state": "offline"}), qos=1, retain=True)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.reconnect_delay_set(1, 30)
        client.connect_async(self.mqtt_host, self.mqtt_port, keepalive=30)
        client.loop_start()
        self.client = client

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):  # noqa: ARG002
        if reason_code.is_failure if hasattr(reason_code, "is_failure") else reason_code != 0:
            log.warning("%s: MQTT connect refused: %s", self.cfg.vehicle, reason_code)
            return
        client.subscribe([(self.topic("config"), 1), (self.topic("command"), 1)])
        versions = {d["deviceId"]: self.cfg.mic_settings(d["deviceId"])["version"] for d in self.cfg.mics}
        client.publish(self.topic("status"), json.dumps({"type": "agent", "state": "online", "agentVersion": "0.5.0",
                                                         "configVersions": versions, "ts": now_iso()}), qos=1, retain=True)
        log.info("%s: connected to %s:%s", self.cfg.vehicle, self.mqtt_host, self.mqtt_port)

    def _publish(self, kind: str, payload: dict, qos: int = 1, retain: bool = False) -> None:
        if self.client is not None:
            self.client.publish(self.topic(kind), json.dumps(payload), qos=qos, retain=retain)

    # control messages ----------------------------------------------------------------------------------
    def _on_message(self, client, userdata, message):  # noqa: ARG002
        if not (message.topic.endswith("/config") or message.topic.endswith("/command")):
            return
        try:
            msg = json.loads(message.payload.decode("utf-8"))
        except ValueError:
            return
        if not isinstance(msg, dict) or msg.get("commandId") is None:
            return
        try:
            self.handle_control(msg)
        except Exception as exc:  # noqa: BLE001
            log.exception("%s: control message failed: %s", self.cfg.vehicle, exc)

    def ack(self, msg: dict, result: str, reported: Optional[dict] = None, reason: Optional[str] = None) -> None:
        body = {"type": "ack", "commandId": msg.get("commandId"), "result": result, "ts": now_iso()}
        if msg.get("deviceId"):
            body["deviceId"] = msg["deviceId"]
        if reported is not None:
            body["reported"] = reported
        if reason:
            body["reason"] = reason
        self._publish("status", body)
        log.info("%s: %s %s -> %s", self.cfg.vehicle, msg.get("type"), msg.get("deviceId") or "", result)

    def handle_control(self, msg: dict) -> None:
        kind = msg.get("type")
        device = msg.get("deviceId")
        known = {d["deviceId"] for d in self.cfg.data.get("devices", [])}
        if device and device not in known:
            return self.ack(msg, "REJECTED", reason="unknown device")
        if kind == "SET_CONFIG":
            s = msg.get("settings") or {}
            gain, thr = s.get("micGainDb"), s.get("detectionThreshold")
            if gain is not None and not GAIN_RANGE[0] <= gain <= GAIN_RANGE[1]:
                return self.ack(msg, "REJECTED", reason="micGainDb out of range")
            if thr is not None and not THRESHOLD_RANGE[0] <= thr <= THRESHOLD_RANGE[1]:
                return self.ack(msg, "REJECTED", reason="detectionThreshold out of range")
            cur = self.cfg.mic_settings(device)
            cur.update({k: v for k, v in s.items() if k in ("micGainDb", "detectionThreshold")})
            cur["version"] = msg.get("version", cur.get("version", 1))
            self.cfg.save()
            return self.ack(msg, "APPLIED", {"version": cur["version"], "micGainDb": cur["micGainDb"],
                                             "detectionThreshold": cur["detectionThreshold"]})
        if kind in ("MUTE", "UNMUTE"):
            self.cfg.mic_settings(device)["muted"] = kind == "MUTE"
            self.cfg.save()
            return self.ack(msg, "APPLIED", {"muted": kind == "MUTE"})
        if kind == "SET_NETWORK":
            profiles = msg.get("profiles") or []
            self.cfg.data["network"] = [{k: p[k] for k in ("priority", "ssid", "securityType") if k in p} for p in profiles]
            self.cfg.save()  # the password is used to join the network and is not written to disk
            return self.ack(msg, "APPLIED", {"profiles": len(profiles)})
        if kind == "RESTART_AGENT":
            self.ack(msg, "APPLIED")
            if self.client is not None:
                self.client.reconnect()
            return None
        return self.ack(msg, "REJECTED", reason=f"unknown command {kind}")

    # telemetry --------------------------------------------------------------------------------------
    def telemetry_once(self) -> dict:
        ctx = self.context.step()
        now = time.time()
        devices = []
        for d in self.cfg.mics:
            fault = self.faults.get(d["deviceId"])
            if fault and fault["until"] < now:
                self.faults.pop(d["deviceId"], None)
                fault = None
            item = {"deviceId": d["deviceId"], "ok": fault is None, "muted": bool(self.cfg.mic_settings(d["deviceId"])["muted"]),
                    "levelDb": round(self.rng.uniform(35, 62), 1)}
            if fault:
                item["reason"] = fault["reason"]
            devices.append(item)
        msg = {"ts": now_iso(), **ctx, "network": self.cfg.data.get("currentNetwork") or {"type": "WIFI", "ssid": "SJSU-Guest",
                                                                                           "rssiDbm": self.rng.randint(-70, -50)},
               "agent": {"version": "0.5.0", "uptimeS": int(now - self.started), "cpuPct": self.rng.randint(5, 25),
                         "outboxDepth": 0}, "devices": devices}
        self._publish("telemetry", msg, qos=0)
        return msg

    def _telemetry_loop(self) -> None:
        while not self.stop_event.wait(10):
            self.telemetry_once()

    # clips and events ---------------------------------------------------------------------------------
    def send_clip(self, device_id: str, samples: np.ndarray, edge_class: str = "unknown", score: float = 0.5) -> Optional[dict]:
        settings = self.cfg.mic_settings(device_id)
        if settings["muted"]:
            log.info("%s: %s is muted; clip dropped", self.cfg.vehicle, device_id)
            return None
        wav = to_wav(apply_gain(samples, settings["micGainDb"] - 6))
        event_id = str(uuid.uuid4())
        ts = now_iso()
        clip = None
        for wait in (0, 1, 2, 4):
            time.sleep(wait)
            try:
                r = self.http.post(f"{self.api_base}/ingest/clips",
                                   headers={"Authorization": f"Bearer {self.cfg.data['api']['ingestToken']}",
                                            "Idempotency-Key": event_id},
                                   files={"file": ("clip.wav", wav, "audio/wav")},
                                   data={"deviceId": device_id, "eventId": event_id, "ts": ts})
            except httpx.HTTPError as exc:
                log.warning("%s: upload failed (%s); retrying", self.cfg.vehicle, exc)
                continue
            if r.status_code in (200, 201):
                clip = r.json()
                break
            if r.status_code == 401:
                log.error("%s: the device token was refused - provision the car again and use the new agent.yaml",
                          self.cfg.vehicle)
                return None
            log.warning("%s: upload refused %s %s", self.cfg.vehicle, r.status_code, r.text[:200])
            if r.status_code < 500 and r.status_code != 429:
                return None
        if clip is None:
            return None
        ctx = self.context.step()
        dtype = next((d["type"] for d in self.cfg.mics if d["deviceId"] == device_id), "MIC_EXTERIOR")
        event = {"eventId": event_id, "tenantId": self.cfg.tenant, "vehicleId": self.cfg.vehicle, "deviceId": device_id,
                 "channel": "interior" if dtype == "MIC_INTERIOR" else "exterior", "ts": ts,
                 "clip": {"clipId": clip["clipId"], "s3Key": clip["s3Key"], "durationMs": clip["durationMs"],
                          "sampleRate": clip["sampleRate"]},
                 "edge": {"class": edge_class, "score": round(score, 2),
                          "priority": "HIGH" if edge_class in ("crash", "siren", "scream", "glass_break") else "NORMAL"},
                 "location": ctx["location"],
                 "context": {"speedKph": ctx["speedKph"], "driveMode": ctx["driveMode"], "ignition": ctx["ignition"]}}
        self._publish("audio/event", event, qos=1)
        self.uploads += 1
        log.info("%s: clip %s (%s) uploaded and event published", self.cfg.vehicle, clip["clipId"], edge_class)
        if self.on_event:
            self.on_event(event)
        return event

    def run_replay(self, bank: SoundBank, min_gap: float = 20, max_gap: float = 60, fault_chance: float = 0.02) -> None:
        """The simulated car: a clip of a random class every 20 to 60 s, now and then a microphone fault."""
        self._start_common()
        mics = [d["deviceId"] for d in self.cfg.mics]
        next_clip = time.time() + self.rng.uniform(5, max_gap)
        while not self.stop_event.wait(1):
            if mics and self.rng.random() < fault_chance / 10 and len(self.faults) == 0:
                mic = self.rng.choice(mics)
                self.faults[mic] = {"reason": self.rng.choice(["NO_SIGNAL", "CLIPPING", "LOW_LEVEL"]),
                                    "until": time.time() + self.rng.uniform(25, 50)}
                log.info("%s: %s simulated fault %s", self.cfg.vehicle, mic, self.faults[mic]["reason"])
            if time.time() >= next_clip and mics:
                cls, samples, source = bank.pick()
                device = next((d["deviceId"] for d in self.cfg.mics if d["type"] == "MIC_INTERIOR"), mics[0]) \
                    if cls == "scream" else next((d["deviceId"] for d in self.cfg.mics if d["type"] == "MIC_EXTERIOR"), mics[0])
                if device not in self.faults:
                    self.send_clip(device, samples, cls, self.rng.uniform(0.55, 0.9))
                next_clip = time.time() + self.rng.uniform(min_gap, max_gap)

    def run_mic(self, device: Optional[str] = None) -> None:
        """The live car: microphone -> pre-filter -> 5 s clip -> upload and publish."""
        mic = MicSource(device)
        mic.start()
        self._start_common()
        mic_device = self.cfg.data.get("micDevice") or (self.cfg.mics[0]["deviceId"] if self.cfg.mics else None)
        pre = PreFilter()
        log.info("%s: listening on the microphone as %s (Ctrl+C to stop)", self.cfg.vehicle, mic_device)
        while not self.stop_event.is_set():
            try:
                block = mic.blocks.get(timeout=1)
            except queue.Empty:
                continue
            threshold = self.cfg.mic_settings(mic_device)["detectionThreshold"]
            if pre.feed(block, threshold, time.time()):
                log.info("%s: pre-filter fired", self.cfg.vehicle)
                samples = mic.cut()
                threading.Thread(target=self.send_clip, args=(mic_device, samples, "unknown", 0.5), daemon=True).start()

    def _start_common(self) -> None:
        self.started = time.time()
        self.connect()
        threading.Thread(target=self._telemetry_loop, daemon=True, name=f"telemetry-{self.cfg.vehicle}").start()
        time.sleep(0.5)
        self.telemetry_once()

    def stop(self) -> None:
        self.stop_event.set()
        if self.client is not None:
            info = self.client.publish(self.topic("status"), json.dumps({"type": "agent", "state": "offline"}),
                                       qos=1, retain=True)
            try:
                info.wait_for_publish(2)      # a clean disconnect sends no last will, so say goodbye first
            except (RuntimeError, ValueError):
                pass
            self.client.disconnect()
            self.client.loop_stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="SCICP edge agent: a live or simulated car")
    parser.add_argument("--config", required=True, help="agent.yaml from the provisioning dialog")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--mic", action="store_true", help="listen to this computer's microphone")
    source.add_argument("--replay", nargs="?", const="", metavar="DATASETS_DIR",
                        help="replay dataset clips (synthetic sounds when no folder is given)")
    parser.add_argument("--state", choices=["driving", "parked"], default="driving")
    parser.add_argument("--api", help="override api.baseUrl (e.g. http://localhost:8080/c2/api/v1)")
    parser.add_argument("--mqtt-host", help="override mqtt.host")
    parser.add_argument("--mqtt-port", type=int, help="override mqtt.port")
    parser.add_argument("--input-device", help="sounddevice input name or index for --mic")
    parser.add_argument("--min-gap", type=float, default=20)
    parser.add_argument("--max-gap", type=float, default=60)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import signal

    def _term(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)   # docker stop and kill: say offline before leaving
    agent = EdgeAgent(AgentConfig.load(args.config), args.state, api_base=args.api, mqtt_host=args.mqtt_host,
                      mqtt_port=args.mqtt_port)
    try:
        if args.mic:
            agent.run_mic(args.input_device)
        else:
            agent.run_replay(SoundBank(args.replay or None, agent.rng), args.min_gap, args.max_gap)
    except KeyboardInterrupt:
        pass
    finally:
        agent.stop()


if __name__ == "__main__":
    main()
