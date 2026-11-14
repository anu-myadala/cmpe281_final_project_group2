# C3 Alert Tracking and Monitoring

The real C3 service (Part III of the design), replacing `stubs/c3.py` without changes to C1.
FastAPI, Python 3.11, package `c3_service`, routes under `/api/v1` (`/c3/api/v1` through nginx).

| Route | What |
|---|---|
| `GET /alerts`, `GET /alerts/map`, `GET /alerts/{alertId}` | the queue, the map pins, the alert page (Table 83) |
| `POST /alerts/{alertId}/actions` | Acknowledge, Resolve, False alarm (BR-C3-07, guarded update) |
| `POST /internal/events/{eventRef}/result` | the classifier worker's result -> at most one alert (Table 85, service token only) |
| `GET /alert-rules?tenantId` | the effective rules: defaults with the tenant's overrides in place (Table 84) |
| `PUT /alert-rules/{ruleId}` | change a rule; on a default with a tenant scope it creates that tenant's override (201); BR-C3-09, BR-C3-10 |
| `DELETE /alert-rules/{ruleId}` | remove an override (204; the default applies again); a default answers 409 |
| `GET /health` | database check |
| C4 worker (`c3/worker/`, own container) | MQTT audio event -> classify -> `audio_events` -> the result route above; `GET /healthz` on :8010 ([below](#c4-classifier-worker)) |

`POST /alert-rules` (a brand-new rule) is **design only** this semester and is not built: it answers
405. Tenants change behaviour by overriding the seven default rules with PUT.

C3 creates only `alert_rule`, `notification` and `sound_class`; `alert`, `alert_action` and
`v_alert_summary` are created by C1. Rule changes are audited in C1's `audit_log` (RULE_SAVE /
RULE_DELETE, the row C1's AuditLogger writes). Events go to C1 with `POST /internal/events`
(`PUBLISHER=noop` drops them); emails go out with `MAIL_MODE=smtp` (MailHog) or `log`.

## Run

```bash
cd c3
.venv/bin/python -m pytest -q                       # SQLite + MongoDB mock, seeded by seed/seed.py
DATABASE_URL=mysql+pymysql://scicp:scicp@127.0.0.1:3306/scicp MONGO_URL=mongodb://127.0.0.1:27017/scicp \
  .venv/bin/uvicorn c3_service.main:app --port 8003   # against docker compose's mysql and mongo
```

## Docker compose (the whole platform with this C3)

```bash
cp .env.example .env        # once; the secrets and URLs are shared with C1
docker compose -f docker-compose.yml -f docker-compose.c3.yml up -d --build
# EC2: -f docker-compose.yml -f docker-compose.aws.yml -f docker-compose.c3.yml, and C3_PUBLIC_BASE_URL=http://<host> in .env
```

| What | URL |
|---|---|
| Web app | http://localhost:8080 |
| C3 API docs (localhost only) | http://localhost:8003/api/v1/docs |
| MailHog (alert emails) | http://localhost:8025 |

`docker-compose.c3.yml` switches C1 over without editing any shared file:

- `c3`: this service (`c3/Dockerfile`), on `c3:8003` inside the network; email through MailHog, events to C1.
- `api`: `C3_BASE_URL=http://c3:8003/api/v1` (C1's AlertClient: overview, map pins).
- `web`: nginx `/c3/` -> `c3:8003`, by mounting `c3/deploy/nginx-default.conf` (C1's file with that one
  upstream line changed; `tests/test_deploy_files.py` fails if the two ever differ in anything else).
- `stubs`: C2 only (`c3/deploy/stubs_c2_only.py`); the cars still move, devices still fault, the simulated
  C4 worker still runs, but `DEMO_MAX_ALERTS=0`: new alerts come only from C3's result call.

## Demo tools

`tools/send_result.py` plays the classifier worker: it stores an audio event at the car's position and
posts its result to C3, which decides (Table 102) and, for an alert, notifies (Table 104) and pushes it live.

```bash
DC="docker compose -f docker-compose.yml -f docker-compose.c3.yml"
$DC exec c3 python tools/send_result.py crash 0.91 AV-01 tA 48            # ACCIDENT/CRITICAL, email to Anh
$DC exec c3 python tools/send_result.py scream 0.83 AV-12 tB 0            # SECURITY/HIGH, email to Maria (not 22:00-06:00 LA)
$DC exec c3 python tools/send_result.py honking 0.80 AV-10 tB 30 --repeat 2   # REPEAT_PENDING, then SAFETY/MEDIUM
$DC exec c3 python tools/send_result.py honking 0.80 AV-12 tB 30          # SERVICE_OFF (SAFETY is off on AV-12)
$DC exec c3 python tools/send_result.py crash 0.95 AV-01 tA 0             # STATE_MISMATCH (crash while parked)
$DC exec c3 python tools/send_result.py traffic 0.97 AV-01 tA 30          # NO_RULE
```

Arguments: `class confidence vehicle tenant speedKph`; options `--also CLASS:CONF` (2nd and 3rd class),
`--ignition on|off`, `--repeat N --gap S`, `--error TEXT` (ERROR_RECORDED), `--model-version`.

`tools/watch_ws.py` prints what C1 pushes to a user over the WebSocket (run it on the host):

```bash
c3/.venv/bin/python c3/tools/watch_ws.py --email maria@bayshuttle.example --seconds 300
```

Since Nov 11, C1's WebSocket hub (`c1/api/app/ws/hub.py`) adds `tenantId` to messages sent to the `staff`
room (tenant rooms still get it stripped). The two staff assertions in `tests/test_publisher_c1.py` now expect it.

## C4 classifier worker

`c3/worker/` (package `c4_worker`, its own requirements and Dockerfile) replaces the stubs' simulated worker:

1. subscribes to `scicp/v1/+/+/audio/event` (QoS 1); tenant and vehicle come from the topic;
2. fetches the 5 s 16 kHz clip with C2's `GET /internal/clips/{clipId}` (service token) and resamples it to 48 kHz;
3. computes the CLAP audio embedding (`laion/clap-htsat-unfused`, 512-d, CPU);
4. classifies it with the ACTIVE model from C1 (a trained head or zero-shot prompts) and keeps the top 3;
5. inserts the `audio_events` document and posts `POST /internal/events/{_id}/result` to C3 (a clip that cannot be fetched or decoded is stored with `inference.error` -> ERROR_RECORDED).

**Model kinds** (`model.pt`, a torch dict; classes in the order siren, crash, glass_break, scream, honking, traffic, environmental):

- `{'kind': 'head', 'state_dict', 'classes', 'embedding_dim': 512}`: Linear(512,128), ReLU, Dropout, Linear(128,7) on the normalized embedding.
- `{'kind': 'zero_shot', 'classes', 'prompts': {class: [texts]}, 'temperature'}`: the worker embeds the prompts when it loads the model and scores by softmax(cosine / temperature).
  The default temperature is 0.05: CLAP's cosines are close together, and 0.01 (an earlier zero-shot build) gave 1.00 for almost every clip, while 0.05 gives about 0.67 - 0.91 on the C2 clips.
  *Older, separate measurement* (the earlier 0.01-temperature build, live run on the simulator's labeled events, not comparable to the seed scores below): zero-shot got about 46% right, correct on siren, traffic and glass_break, wrong on honking, environmental, scream and crash.

**Seeded versions** (from `seed/models/README.md`, which has the data, the split and the per-class table; every number is in `seed/models/evaluation.json`):

- `1.1.0`: CLAP zero-shot with text prompts, temperature 0.05 (the worker's default), no training. ACTIVE after the seed. Macro-F1 0.68, accuracy 0.72.
- `1.2.0`: a trained head (512 -> 128 -> 7) on CLAP embeddings. REGISTERED. Macro-F1 0.99, accuracy 0.99.
- Both are real `model.pt` artifacts the worker loads, scored on the same 237 test clips (787 clips from the course `Audio_Datasets`, stratified split 550 / 237, `random_state=281`; 1.2.0 never saw the test clips).
- **Caveat**: the head's score comes from a random file-level split, and files of one class may be cut from the same longer recordings, so 0.99 is likely optimistic for new audio.

**Upload and activate** at http://localhost:8080 -> AI models, as `admin@scicp.example` / `Passw0rd!demo`
(only SERVICE_STAFF and SYS_ADMIN can write to the registry). The main example is the one in `seed/models/README.md`
and `deploy/DEMO_SCRIPT.md`: version `1.3.0`, which the seed does not register.

- *Upload new version*: version `1.3.0`, the file `seed/models/1.3.0/model.pt`, macro-F1 0.99, accuracy 0.99, and the per-class block from `seed/models/1.3.0/per-class.json`. The browser computes the SHA-256 itself.
  Per the seed README, 1.3.0 is the same head as 1.2.0 trained with label smoothing 0.1 on the same 550 training clips and scored on the same 237 test clips, so the same caveat applies: its scores are likely higher than on new audio.
  Its mean top confidence on the test clips is 0.88 against 0.99 for 1.2.0, which matters because C3 compares the confidence with its rule thresholds.
- *Set as active*: BR-10 fails only when macro-F1 is more than 0.05 below the active version. With the seed's 1.1.0 (0.68) active, 1.2.0 and 1.3.0 (0.99) pass without *Force*.
- The *Deployment* card shows `c4-worker-1` go LOADING, then READY with the SHA-256 verified (or FAILED with the reason).

**Optional: build a zero-shot model** (needs torch only; the venv commands are in `c3/worker/requirements-dev.txt`):

```bash
c3/worker/.venv/bin/python c3/worker/make_zero_shot_model.py     # -> $SCICP_DATA/work/models/zero_shot_1.3.1/
# options: --version X.Y.Z (output folder zero_shot_<version>), --temperature T (default 0.05; higher = softer)
# writes model.pt, model.pt.sha256 and metrics.json ({macroF1, accuracy, perClass}; zeros: not evaluated)
```

Upload it the same way as version `1.3.1` with the values from its `metrics.json`. Those are zeros (not evaluated),
so BR-10 fails against the active version: tick *Force* and write a note to activate it.

**Run** (the five-file command from `docker-compose.c4.yml`; it also sets `WORKER_ENABLED=0` on the stubs, because
`DEMO_ENABLED=0` alone still starts their simulated worker):

```bash
docker compose -f docker-compose.yml -f docker-compose.c3.yml -f docker-compose.c2.yml \
               -f docker-compose.c2c3.yml -f docker-compose.c4.yml up -d --build
curl -s http://127.0.0.1:8010/healthz     # {status, modelVersion, queueDepth, processed, dropped, errors}
cd c3/worker && .venv/bin/python -m pytest -q     # unit tests: fake encoder and head, no model download
```

The CLAP weights (about 600 MB) download on the first start into the named volume `c4-hf-cache`, then load from it.

**Design decisions**

- **Ack after store**: the MQTT message is acknowledged only once the event is in `audio_events`; a redelivered event (same tenant and `eventId`) reuses the stored `_id`.
- **One clip at a time**: a single consumer; fetch, inference, store and the result call finish before the next event.
- **Priority queue of 200**: HIGH before NORMAL; when full, the oldest NORMAL is dropped and counted. Mosquitto's default of 20 unacknowledged messages per client keeps the queue far below 200 in practice.
- **Model swap only between clips**: a new version is downloaded, verified (size and SHA-256) and loaded alongside the current one, then swapped in between two clips. A bad artifact reports FAILED and the previous model keeps serving.
- **No model, no processing**: events wait in the queue (and in the broker) until a version loads. C1 is polled every 30 s besides the `model/activated` MQTT wake-up (every 5 s while nothing is loaded and C1 has no ACTIVE version or is unreachable).

## Demo checklist

```bash
DC="docker compose -f docker-compose.yml -f docker-compose.c3.yml -f docker-compose.c2.yml -f docker-compose.c2c3.yml -f docker-compose.c4.yml"
eval $DC up -d --build                        # 1. the stack (cp .env.example .env once before)
curl -s http://127.0.0.1:8010/healthz         # 2. the seed's 1.1.0 is ACTIVE; the worker loads it
# 3. web: admin@scicp.example -> AI models -> Upload new version 1.3.0 (seed/models/1.3.0/model.pt,
#    macro-F1 0.99, accuracy 0.99, seed/models/1.3.0/per-class.json)
# 4. web: Set as active 1.3.0 (no Force: 0.99 is not below the active 1.1.0's 0.68)
eval $DC logs -f c4                           # 5. LOADING -> READY, then one line per classified event
curl -s http://127.0.0.1:8010/healthz         # 6. modelVersion sound-classifier-1.3.0
# 7. web: the alert monitor gets the alerts C3 decides from the simulated cars' events
```

## Known seed artifacts

`seed/seed.py` (owned by C1) builds the alert history without applying the alert rules, so 17 of
the 41 seeded alerts would not be created by the AlertEngine. They stay in the data as history;
`tests/test_engine_seed_consistency.py` pins them and fails on any other disagreement.

| Seeded alerts | Engine decision | Reason |
|---|---|---|
| 20443, 20446, 20452, 20455, 20457, 20458, 20460, 20461, 20465, 20476, 20478 (honking, SAFETY) | REPEAT_PENDING | one honking event each; BR-C3-05 needs 2 within 10 s |
| 20444, 20451 (honking on AV-12, SAFETY) | SERVICE_OFF | the seeded subscription has SAFETY off on AV-12 (scenario SC2) |
| 20441, 20454, 20480 (glass_break), 20453 (crash), ACCIDENT | BELOW_THRESHOLD | confidence 0.632 - 0.671, under the 0.70 minimum of rules 2 and 3 |
