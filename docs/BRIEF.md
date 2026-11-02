# SCICP C1 implementation brief (Deliverable #3)

Component C1 "Cloud Platform Dashboard and System Admin" of the Smart Car Intelligent Cloud Platform (SCICP),
with the C4 model registry pages. Owner: Anukrithi Myadala, CMPE 281 Group 2, SJSU, Fall 2026.
Due Nov 11, 2026: live demo (UI + backend) and a recorded scenario demo.

The design is fixed by Deliverable #2. Source of truth, in this order:
1. `docs/spec_DESIGN.md` (master list of APIs, classes, tables, rules, seed data).
2. `docs/spec_part1_c1.txt` (the full Part I text with every API table, decision table, DDL, pseudocode, GUI tables).
3. `docs/spec_overview.txt` (interfaces between components), `docs/spec_part2_c2.txt`, `docs/spec_part3_c3.txt`
   (what C2 and C3 expect from C1 and what C1 calls on them).
Where the text and this brief differ, this brief wins (it contains the late changes made when the three parts were merged).

## Late changes that override the DESIGN.md text
- `model.activated` is published twice after the activation commits: on the in-process EventBus (WsHub forwards it to the staff room)
  and on the MQTT topic `scicp/v1/platform/model/activated` (QoS 1, retained, payload `{modelId, version}`) for the C4 classifier worker.
  MQTT is optional at runtime: if `MQTT_URL` is unset, log and skip.
- `tenant` has a `time_zone VARCHAR(40) DEFAULT 'America/Los_Angeles'` column; `POST /admin/tenants` accepts optional `timeZone`.
- `alert.updated` WebSocket message: `{type, alertId, status, by}` where `by` is the display name of the user.
- `device.status` message carries optional `faultReason?`, `muted?`, `config?{version, state}`; forward unknown fields unchanged.
- `GET /admin/tenants` is readable by IOT_ENGINEER and SERVICE_STAFF too (tenant selector); every other /admin call is SYS_ADMIN only.
- Pagination: `page` (1-based) and `size`, default 25, maximum 100 (400 when over 100).
- alert_type seed: ACCIDENT CRITICAL 10 min, EMERGENCY HIGH 5, SECURITY HIGH 15, SAFETY MEDIUM 30, PASSENGER LOW 60, VEHICLE_HEALTH LOW 120.
- The subscription row stores `vehicle_id` as the BIGINT key of the C2 `vehicle` table; the API uses the public code (AV-01). Resolve codes through the vehicle table.
- AuditLogger is shared: C2/C3 write RETIRE_VEHICLE, PROVISION, DELETE_CLIP, RULE_SAVE, RULE_DELETE through it (just accept any action string).
- Buckets: `scicp-models` (artifacts, key `models/{modelId}/{version}/model.pt`), `scicp-clips` (C2). Region us-west-2 on AWS.
  Locally (Nov 1: MinIO no longer publishes Docker images) the API stores the artifacts itself: `STORAGE_BACKEND=local`, `STORAGE_DIR=/data/objects`
  on the named volume `objects`, presigned URLs = relative HMAC-signed `/api/v1/storage/objects/<key>?op&exp&sig`; `STORAGE_BACKEND=s3` on EC2.
- Model deployment (Nov 1, the instructor asks for "UI and model management, and deployment" for AI components): the C4 classifier worker
  (simulated in stubs/) reloads on model.activated (MQTT, EventBus inline, 30 s poll), downloads the active artifact through C1
  (`GET /internal/models/active`), verifies the SHA-256 and reports `POST /internal/worker/status`; `GET /models/{id}/deployment` and the
  WebSocket message `model.deployed` drive the Deployment card on the models page.
- Feedback: the C3 alert page posts FALSE_ALARM on "False alarm" and CONFIRMED on "Resolve".
- VEHICLE MAP (added Oct 30 after the instructor asked for tracking on a map; priority HIGH, it is the demo's centerpiece):
  - `GET /dashboard/map?tenantId` -> `{vehicles[{vehicleId, label, status, connection, lat, lon, headingDeg, speedKph, driveMode, ignition, sampleAt}], alerts[{alertId, vehicleId, lat, lon, severity, type, createdAt}], asOf, degraded?}`.
    Roles like /dashboard/overview (403 RIDER, IOT_ENGINEER; staff pass tenantId). Cars without a sample: lat/lon/sampleAt null.
    Positions: `EventQueryRepo.latest_positions(tenantId)` = one aggregation on the MongoDB `vehicle_telemetry` collection
    (`$match meta.tenantId`, `$sort ts desc`, `$group _id: meta.vehicleId, first doc`), cached 10 s per tenant. Documents:
    `{ts, meta{tenantId (tenant code, e.g. "tA"), vehicleId (code "AV-01")}, speedKph, driveMode, ignition, location{type:"Point", coordinates:[lon, lat]}, network{...}, agent{...}, devices[...]}`.
    headingDeg is computed from the last two samples (bearing) when the car moved; connection = ONLINE when the newest sample is < 30 s old, else OFFLINE; a car with no sample keeps the vehicle.status only.
    Alert pins: `AlertClient.map_points(tenantId)` -> C3 `GET /alerts/map?status=OPEN&tenantId` (stub implements it; returns the alert's event location). When C3 fails, return the last cached pins and `degraded: true`.
  - Trail of one car: the web app calls C2 `GET /vehicles/{vehicleId}/telemetry?limit=60` (newest first; the stub serves it from the same MongoDB collection) and draws a polyline.
  - Front end: `VehicleMap` component (react-leaflet 4 + leaflet 1.9, OpenStreetMap tiles; on EC2 without internet to tile.openstreetmap.org the map still shows markers over a plain background) with one circle marker per car (green ONLINE, amber OFFLINE, gray no sample; label = car code; heading arrow when speedKph > 0), one pin per open alert in severity color (CRITICAL red, HIGH orange, MEDIUM yellow, LOW gray), the trail polyline of the selected car; keeps zoom/center between refreshes unless "Follow" is on. Used on the Overview page (card, "Open full map" button) and on the new `/map` page (`VehicleMapPage`: filters row, map, car list + selected-car detail with Open vehicle / Alerts of this car / Subscriptions buttons). Refetch every 10 s; alert.created/updated add/remove pins at once; device.status recolors the marker.
  - Seed and demo generator: `vehicle_telemetry` gets ~10 min of samples per ONLINE car around SJSU (37.3352, -121.8811), one every 10 s, moving along plausible paths; the stubs' demo event generator keeps appending a new sample every 10 s for the online cars so markers move during the live demo. AV-13 (OFFLINE) has samples that stopped 14 min ago; AV-14 (REGISTERED) has none.
  - Menu: "Vehicle map" entry for OWNER and FLEET_MANAGER (after Overview); staff reach the map through the overview card for the selected tenant.

## Repository layout (monorepo shared with Brandon (C2) and Ray (C3) later)
```
README.md                 how to run locally, on AWS, and the demo script
docker-compose.yml        local: mysql, mongo, mailhog, mosquitto, api, web, stubs, seed (volume objects for the artifacts)
docker-compose.aws.yml    overrides for EC2 (real S3/SES, no mailhog)
deploy/                   nginx.conf, EC2 bootstrap script, .env.example
c1/api/                   FastAPI backend (Python 3.11)   -> package `app`
c1/web/                   React 18 + TypeScript + Vite + MUI + Recharts + TanStack Query
stubs/                    stub C2 and C3 (FastAPI) + demo event generator, replaced by the real components later
seed/                     seed.py: MySQL + MongoDB seed with the Deliverable #1/#2 sample data
docs/                     this brief and the specs
```

## Conventions (all three parts of the platform follow them)
- REST under `/api/v1`, JSON, ISO-8601 UTC timestamps (`2026-11-04T21:42:07Z`), camelCase in JSON, snake_case in SQL.
- Bearer JWT (HS256, 15 min) with claims `sub` (userId), `tenantId` (null for staff), `role`, `name`, `plan`, `jti`, `exp`.
  Refresh token: opaque random, stored hashed (SHA-256) in `refresh_token`, 7 days, delivered as httpOnly cookie `refreshToken`.
- Error envelope: `{"error": {"code": "...", "message": "...", "details": {...}}}`; codes UNAUTHORIZED 401, FORBIDDEN 403,
  NOT_FOUND 404, CONFLICT 409, LOCKED 423, VALIDATION 400, BUSINESS_RULE 422 (details.rule = "BR-xx"), RATE_LIMIT 429.
  FastAPI's own 422 validation errors must be converted to this envelope with code VALIDATION and HTTP 400.
- Tenant scope comes from the token (TenantContext), never from the URL; staff roles pass `tenantId` as a query parameter.
- Identifiers in the API: `vehicleId` is the public vehicle code (`AV-01`); `tenantId` is the numeric id; `eventRef` is a 24-char ObjectId string.
- Service token for internal calls: JWT with role `SERVICE`, 24 h, signed with `SERVICE_TOKEN_SECRET`; `/internal/*` routes accept only it.
- Every write audited (BR-12) with before/after JSON.
- Passwords: argon2id (argon2-cffi). Seed users all have password `Passw0rd!demo` except where the demo needs otherwise.

## Environment variables (`.env`)
```
DATABASE_URL=mysql+pymysql://scicp:scicp@mysql:3306/scicp
MONGO_URL=mongodb://mongo:27017/scicp            (or "mock" -> mongomock_motor, used by tests)
JWT_SECRET=...  SERVICE_TOKEN_SECRET=...
STORAGE_BACKEND=local|s3  STORAGE_DIR=/data/objects (local)
S3_ACCESS_KEY, S3_SECRET_KEY (empty with the instance role), S3_REGION=us-west-2, S3_MODELS_BUCKET=scicp-models (s3)
MAIL_MODE=smtp|ses|log  SMTP_HOST=mailhog SMTP_PORT=1025  MAIL_FROM=no-reply@scicp.example
MQTT_URL=mqtt://mosquitto:1883 (optional) MQTT_USERNAME=svc-api MQTT_PASSWORD=...
C2_BASE_URL=http://stubs:8002/api/v1  C3_BASE_URL=http://stubs:8003/api/v1
CORS_ORIGINS=http://localhost:5173,http://localhost:8080
```

## Running modes (all three must work)
- `docker compose up` on a laptop or EC2: separate containers mysql, mongo, mailhog, mosquitto, api (:8000), web (nginx :8080 serving the Vite build and proxying /api and /ws to api), stubs (:8002 C2, :8003 C3), seed (one-shot).
- Developer / CI mode without Docker (what the container used to build this has: MariaDB on localhost, no MongoDB daemon, no Docker):
  `MONGO_URL=mock` makes the API use `mongomock_motor.AsyncMongoMockClient` in process, and `STUBS_INLINE=1` mounts the C2 and C3 stub routers
  inside the same FastAPI app (under `/c2/api/v1` and `/c3/api/v1`, with `C2_BASE_URL`/`C3_BASE_URL` pointing at itself) so that one process is a complete demo;
  in that mode the API seeds MySQL and the in-process Mongo itself at startup when `SEED_ON_START=1` (it imports `seed/seed.py` as a module). The stubs' inline mode
  publishes events straight on the EventBus instead of calling `POST /internal/events`.
- Tests: `pytest` with `DATABASE_URL=sqlite+aiosqlite:///./test.db` (or the MariaDB URL when set) and `MONGO_URL=mock`.
- Python: write for 3.11 (Docker image python:3.11-slim) but it must also run on 3.13 (the build container). Pin `sqlalchemy>=2.0,<2.2`.

## Internal endpoints C1 exposes for the other components (service token)
- `POST /internal/events` body `{topic: "alert.created"|"alert.updated"|"device.status", payload: {...}, tenantId}` -> 202; publishes on the EventBus
  (this is how the separate C2/C3 containers reach the in-process bus; when their modules run inside this app they call EventBus.publish directly).
- `POST /internal/subscriptions/defaults` body `{vehicleId (public code), tenantId}` -> 201 (SubscriptionService.defaults; called by C2 on vehicle creation).
- `GET /internal/subscriptions?vehicleId&tenantId` -> the rows C3 reads (service switch, channels, quiet hours, notify_riders, tenant time zone).

## What C1 calls on C2 and C3 (configurable base URLs; the stubs implement exactly this)
- C2: `GET /vehicles?tenantId` -> `{items[{vehicleId, vin, label, make, model, year, status, connection ONLINE|OFFLINE|NEVER, lastSeenAt, retentionDays, deviceSummary{total, online, offline, fault, disabled}}], page, size, total}`
  and `GET /vehicles/{vehicleId}/devices` -> `[{deviceId, type, status, lastSeenAt}]`.
- C3: `GET /alerts?status=OPEN&tenantId&limit` -> `{items[{alertId, vehicleId, type, severity, status, topClass, confidence, createdAt, ageSeconds, acknowledgedBy}], total}`
  and `GET /alerts/{alertId}`.
- Timeouts 5 s; on failure the overview answers with cached cards (TTL 30 s) and `degraded: true`.

## Database
- MySQL 8 in Docker; tests run against MariaDB/MySQL or SQLite. Keep the DDL of Part I Section 3.3 (tables tenant, app_user, refresh_token,
  intelligence_service, subscription, alert_type, audit_log, ai_model, model_version, feedback_label) plus, for the demo, the C2/C3 tables
  that C1 reads (`vehicle`, `vehicle_rider`, `alert`, `alert_action`, view `v_alert_summary`) created by the seed with the columns of Parts II/III.
- Use SQLAlchemy 2.x declarative models + `Base.metadata.create_all` at startup (no Alembic needed); `channels` as a comma-separated VARCHAR
  on SQLite and SET on MySQL is acceptable: implement as VARCHAR(40) everywhere.
- MongoDB `audio_events` documents follow Part III Table "Fields of audio_events".

## Seed data (seed/seed.py, idempotent)
Tenants: 1 "Nguyen household" (OWNER, BASIC, max 3, code tA), 2 "Bay Shuttle LLC" (FLEET, FLEET, max 50, code tB).
Users: anh@example.com OWNER tenant 1 "Anh Nguyen"; maria@bayshuttle.example FLEET_MANAGER tenant 2 "Maria Lopez"; rider@example.com RIDER tenant 1;
iot@scicp.example IOT_ENGINEER "D. Patel"; staff@scicp.example SERVICE_STAFF "Service Staff"; admin@scicp.example SYS_ADMIN "Platform Admin". Password `Passw0rd!demo`.
Vehicles: AV-01 (tenant 1, VIN 1HGCM82633A004352, Honda Civic, ACTIVE); AV-10..AV-14 (tenant 2, Tesla Model 3 "Shuttle 1..5", AV-13 OFFLINE, AV-14 REGISTERED).
Subscriptions: defaults for every vehicle (EMERGENCY, ACCIDENT, SECURITY, SAFETY enabled with DASHBOARD,EMAIL; AV-12 SAFETY off, SECURITY quiet 22:00-06:00, notify_riders on).
Alerts (MySQL `alert`): 20482 ACCIDENT CRITICAL NEW crash 0.91 AV-01 event_ref 6729b1c4e1f0a2d3c4b5a697 created 2026-11-04T21:42:12Z; plus ~40 alerts over the last 14 days across both tenants
with mixed types, statuses (NEW/ACKNOWLEDGED/RESOLVED/FALSE_ALARM) and acknowledged_at/closed_at so statistics are meaningful.
audio_events (Mongo): ~400 events over the last 14 days, classes weighted (traffic, environmental most common), with inference.topK, modelVersion "sound-classifier-1.1.0", decision for the alert ones.
Models: ai_model "sound-classifier" (task "audio event classification (CLAP + head)"); versions 1.1.0 ACTIVE macroF1 0.84 acc 0.88, 1.2.0 REGISTERED 0.86/0.89, both with perClass metrics for the 7 classes, artifacts stored (local volume or bucket) as small dummy files with correct sha256.
Feedback labels: ~15 on version 1.1.0.

## Quality bar
- Backend: pytest suite (one test per column of the decision tables for BR-02 login, BR-04 subscription, BR-06/BR-07 admin, BR-08/09 range, BR-10 activation, BR-11 feedback; tenancy tests with two tenants expecting 404/403; WebSocket fan-out test).
- Frontend: typecheck (`tsc --noEmit`) and build pass; pages render with the seed data; Playwright smoke test of login -> overview -> subscriptions -> admin -> models.
- No boilerplate or watermark comments; comments explain design intent only. Commit author "Anukrithi Myadala <anukrithi.myadala@sjsu.edu>".
- Code style: clear names from the design (AuthService, LoginThrottle, TokenService, RbacGuard, TenantContext, DashboardService, SubscriptionService,
  ReportService, AdminService, ModelRegistryService, AuditLogger, WsHub, EventBus, MailSender, StorageClient, VehicleClient, AlertClient, repositories *Repo).
