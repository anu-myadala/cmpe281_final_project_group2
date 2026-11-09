# C2 Edge Agent and IoT Device Management

The real C2 service (Part II of the design, Brandon Rivera Venegas), replacing `stubs/c2.py` without
changes to C1 or C3, plus the edge agent that plays the cars. FastAPI, Python 3.11, package
`c2_service`, routes under `/api/v1` (`/c2/api/v1` through nginx).

| Route | What |
|---|---|
| `GET /vehicles`, `GET /vehicles/{id}`, `GET /vehicles/{id}/devices`, `GET /vehicles/{id}/telemetry`, `GET /devices` | the stub's contract (C1's overview, vehicle pages and map), now from real tables |
| `POST /vehicles`, `PUT /vehicles/{id}`, `DELETE /vehicles/{id}?confirm=true` | add (BR-C2-02 plan limit, BR-C2-03 identity; C1's default subscriptions), edit, retire (BR-C2-04) |
| `POST /vehicles/{id}/devices`, `PUT /devices/{id}`, `DELETE /devices/{id}` | devices (BR-C2-05, -06), enable / disable (BR-C2-10) |
| `POST /vehicles/{id}/provision` (`{rotate}`), `DELETE /vehicles/{id}/provision` | MQTT login, device token and agent.yaml, shown once (BR-C2-07 to -09) |
| `PUT /devices/{id}/config`, `POST /devices/{id}/commands`, `GET /devices/{id}/commands`, `GET /commands/{id}` | gain and threshold, MUTE / UNMUTE / RESTART_AGENT; 202, confirmed when the car acks (BR-C2-11 to -13) |
| `GET`, `PUT /vehicles/{id}/network` | Wi-Fi profiles, passwords encrypted and write-only (BR-C2-25) |
| `POST /ingest/clips` | the car's upload with its device token (BR-C2-14 to -17, FC1) |
| `GET /vehicles/{id}/clips`, `GET /clips/{id}/url`, `DELETE /clips/{id}`, `PUT /vehicles/{id}/retention` | the Data tab: list, play (signed link), delete, retention (BR-C2-22 to -24) |
| `GET /internal/clips/{clipId}` | for Ray's C4 worker: the 5 s mono 16 kHz WAV (service token only) |
| `POST /internal/jobs/retention` | runs the daily retention job now (service token) |
| `GET /health` | database and MQTT state |

MQTT (Mosquitto): C2 listens to `scicp/v1/+/+/telemetry` (every 10 s, the heartbeat) and
`scicp/v1/+/+/status` (online, last will, device faults, command acks) and publishes on
`.../config` and `.../command`. The cars publish `.../audio/event` (QoS 1) with
`clip{clipId, s3Key, durationMs, sampleRate}` straight to the worker; C2 does not relay it.
Device status changes go to C1 as `device.status` with `POST /internal/events`.

C2 creates only `device`, `device_command`, `network_config` and `clip`; `vehicle` and
`vehicle_rider` are created and seeded by C1 in this repository and C2 writes their rows. On
first start C2 gives every seeded car the three devices the stub showed (dev-NN10 gateway,
dev-NN11 interior mic, dev-NN12 exterior mic).

## Run the platform with C2

```bash
cp .env.example .env                       # once
docker compose -f docker-compose.yml -f docker-compose.c2.yml up -d --build
# with Ray's C3 as well:
docker compose -f docker-compose.yml -f docker-compose.c3.yml -f docker-compose.c2.yml -f docker-compose.c2c3.yml up -d --build
```

| What | URL |
|---|---|
| Web app (C1) | http://localhost:8080 |
| **C2 console** (vehicles, devices, provisioning, network, data) | http://localhost:8080/c2/console/ |
| C2 API docs (localhost only) | http://localhost:8012/api/v1/docs |

`docker-compose.c2.yml` switches the platform over without editing any shared file:

- `c2`: this service, `c2:8002` inside the network, clips on the `clips` volume.
- `c2-sim`: the simulated cars AV-01, AV-10, AV-11, AV-12. It signs in as the IoT engineer,
  provisions each car through C2, then sends telemetry every 10 s and a clip every 30-90 s.
  Put the course datasets in `c2/edge/datasets/<class>/` (siren, crash, glass_break, scream,
  honking, traffic, environmental); without them it uses synthetic sounds. AV-13 stays offline
  and AV-14 never connects, as in the seed.
- `api`: `C2_BASE_URL=http://c2:8002/api/v1`.
- `web`: nginx `/c2/` -> `c2:8002` (`c2/deploy/nginx-default.conf`, C1's file with that one line changed;
  `nginx-c2-c3.conf` changes the C3 line too).
- `stubs`: `DEMO_ENABLED=0`, so the stub generator stops writing telemetry and faults (the cars do);
  the simulated C4 worker keeps running until Ray's worker replaces it.

## The live car (your laptop's microphone)

1. In the console (signed in as `iot@scicp.example`), pick Nguyen household, **+ Add vehicle**
   AV-02, add an exterior microphone, then **Provision agent** and download `agent.yaml`.
2. On the laptop:
   ```bash
   pip install -r c2/edge/requirements.txt sounddevice
   python c2/edge/agent.py --config agent.yaml --mic            # --state parked for a parked car
   ```
3. AV-02 turns ACTIVE and its devices ONLINE within seconds; clap or play a siren near the
   microphone and the clip shows up in the Data tab.

Do not use AV-01 or AV-10..12 for the live car: the simulator rotates their credentials when it starts.

## Tests

```bash
cd c2
python -m venv .venv && .venv/bin/pip install -r requirements.txt -r ../c1/api/requirements.txt
.venv/bin/python -m pytest -q          # 25 tests: the stub contract, tenancy, the decision tables of Section 4,
                                       # the agent's control handling, the nginx files
```

The tests use SQLite and the MongoDB mock, seeded by the real `seed/seed.py`, and a fake MQTT
gateway. `python tools/dev_harness.py` runs C2 alone (with a stand-in for C1's login) for UI work;
it needs a broker on localhost:1883.

## On EC2

Add the overlay after the AWS file: `docker compose -f docker-compose.yml -f docker-compose.aws.yml -f docker-compose.c2.yml up -d --build`
(plus `-f docker-compose.c3.yml ... -f docker-compose.c2c3.yml` with C3). In `.env` set
`C2_PUBLIC_API_BASE=http://<public-host>/c2/api/v1` and `C2_PUBLIC_MQTT_HOST=<public-host>` so agent.yaml points
at the instance; a laptop car on EC2 also needs port 1883 open to your IP (the AWS file closes it), so on EC2
the demo uses the simulated cars. `C2_CLIP_BACKEND=s3` keeps the clips in the `scicp-clips` bucket.

## Known limits this semester

- The course Mosquitto allows anonymous clients inside the compose network (`deploy/mosquitto.conf`),
  so the per-car MQTT logins are issued and recorded but not enforced by the broker
  (`brokerAuth: "none"` in the provisioning answer). The design's dynamic-security setup is the next step.
- FIRMWARE_UPDATE answers 501, the cellular APN is design only, riders are read from the seed only.
- Clips are stored on the `clips` volume; `C2_CLIP_BACKEND=s3` uses the `scicp-clips` bucket on EC2.
