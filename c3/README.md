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

## Known seed artifacts

`seed/seed.py` (owned by C1) builds the alert history without applying the alert rules, so 17 of
the 41 seeded alerts would not be created by the AlertEngine. They stay in the data as history;
`tests/test_engine_seed_consistency.py` pins them and fails on any other disagreement.

| Seeded alerts | Engine decision | Reason |
|---|---|---|
| 20443, 20446, 20452, 20455, 20457, 20458, 20460, 20461, 20465, 20476, 20478 (honking, SAFETY) | REPEAT_PENDING | one honking event each; BR-C3-05 needs 2 within 10 s |
| 20444, 20451 (honking on AV-12, SAFETY) | SERVICE_OFF | the seeded subscription has SAFETY off on AV-12 (scenario SC2) |
| 20441, 20454, 20480 (glass_break), 20453 (crash), ACCIDENT | BELOW_THRESHOLD | confidence 0.632 - 0.671, under the 0.70 minimum of rules 2 and 3 |
