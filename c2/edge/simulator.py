"""The simulated fleet (the c2-sim container of docker-compose.c2.yml).

It does what an IoT engineer and the cars would do: it logs in to C1 as the IoT engineer,
provisions each car through C2 (rotate for a car that already has credentials), and runs one
EdgeAgent per car in its own thread, replaying dataset clips (or synthetic ones) and sending
telemetry every 10 s. Cars in SIM_CARS move on the map; AV-13 stays offline and AV-14 never
connects, as in the seed.

Environment:
  SIM_CARS          tA:AV-01,tB:AV-10,tB:AV-11,tB:AV-12
  C1_BASE_URL       http://api:8000/api/v1    (login)
  C2_URL            http://c2:8002/api/v1      (provisioning and uploads)
  SIM_MQTT_HOST     mosquitto   SIM_MQTT_PORT 1883
  SIM_ENGINEER_EMAIL iot@scicp.example   SIM_ENGINEER_PASSWORD Passw0rd!demo
  DATASETS_DIR      /datasets (optional: datasets/<class>/*.wav)
  SIM_MIN_GAP / SIM_MAX_GAP  seconds between clips per car (default 30 / 90)
"""
from __future__ import annotations

import logging
import os
import random
import signal
import sys
import threading
import time
from pathlib import Path

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import AgentConfig, EdgeAgent  # noqa: E402
from sounds import SoundBank  # noqa: E402

log = logging.getLogger("scicp.sim")


def login(c1: str, email: str, password: str) -> str:
    for attempt in range(60):
        try:
            r = httpx.post(f"{c1}/auth/login", json={"email": email, "password": password}, timeout=10)
            if r.status_code == 200:
                return r.json()["accessToken"]
            log.info("login answered %s; retrying", r.status_code)
        except httpx.HTTPError as exc:
            log.info("C1 not reachable yet (%s); retrying", exc)
        time.sleep(5)
    raise SystemExit("could not log in to C1")


def provision(c2: str, token: str, tenant: str, vehicle: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    for attempt in range(60):
        try:
            r = httpx.post(f"{c2}/vehicles/{vehicle}/provision", params={"tenantId": tenant}, json={"rotate": False},
                           headers=headers, timeout=10)
            if r.status_code == 409:
                r = httpx.post(f"{c2}/vehicles/{vehicle}/provision", params={"tenantId": tenant}, json={"rotate": True},
                               headers=headers, timeout=10)
            if r.status_code == 201:
                return r.json()
            log.warning("provisioning %s answered %s %s", vehicle, r.status_code, r.text[:200])
            if r.status_code < 500:
                raise SystemExit(f"cannot provision {vehicle}")
        except httpx.HTTPError as exc:
            log.info("C2 not reachable yet (%s); retrying", exc)
        time.sleep(5)
    raise SystemExit("could not provision")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cars = [c.split(":") for c in os.environ.get("SIM_CARS", "tA:AV-01,tB:AV-10,tB:AV-11,tB:AV-12").split(",") if c]
    c1 = os.environ.get("C1_BASE_URL", "http://api:8000/api/v1").rstrip("/")
    c2 = os.environ.get("C2_URL", "http://c2:8002/api/v1").rstrip("/")
    mqtt_host = os.environ.get("SIM_MQTT_HOST", "mosquitto")
    mqtt_port = int(os.environ.get("SIM_MQTT_PORT", "1883"))
    datasets = os.environ.get("DATASETS_DIR") or None
    min_gap, max_gap = float(os.environ.get("SIM_MIN_GAP", 30)), float(os.environ.get("SIM_MAX_GAP", 90))
    token = login(c1, os.environ.get("SIM_ENGINEER_EMAIL", "iot@scicp.example"),
                  os.environ.get("SIM_ENGINEER_PASSWORD", "Passw0rd!demo"))
    agents = []
    for i, (tenant, vehicle) in enumerate(cars):
        result = provision(c2, token, tenant, vehicle)
        cfg = AgentConfig(None, yaml.safe_load(result["agentYaml"]))
        agent = EdgeAgent(cfg, "driving", seed=1000 + i, api_base=c2, mqtt_host=mqtt_host, mqtt_port=mqtt_port)
        bank = SoundBank(datasets, random.Random(2000 + i))
        threading.Thread(target=agent.run_replay, args=(bank, min_gap, max_gap), daemon=True, name=vehicle).start()
        agents.append(agent)
        log.info("simulated car %s/%s started (credential v%s, sounds from %s)", tenant, vehicle,
                 result["credentialVersion"], datasets or "the synthetic bank")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.wait(60):
        log.info("fleet: %s", ", ".join(f"{a.cfg.vehicle} {a.uploads} clips" for a in agents))
    for a in agents:
        a.stop()


if __name__ == "__main__":
    main()
