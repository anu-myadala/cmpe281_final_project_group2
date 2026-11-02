# SCICP C1 API

FastAPI backend of component C1 (Cloud Platform Dashboard and System Admin) with the C4 model
registry endpoints. Package `app`; entry point `app.main:app` (factory `app.main:create_app`).
All REST routes live under `/api/v1`, the WebSocket at `/ws` (alias `/api/v1/ws`), the OpenAPI
document at `/api/v1/docs`.

## Run

Developer mode, one process, no Docker (SQLite + in-process MongoDB mock, seeded at startup):

```bash
cd c1/api
pip install -r requirements.txt
cp .env.example .env            # optional
DATABASE_URL=sqlite+aiosqlite:///./dev.db MONGO_URL=mock SEED_ON_START=1 STORAGE_DIR=./objects \
  uvicorn app.main:app --reload --port 8000
```

Add `STUBS_INLINE=1 C2_BASE_URL=http://localhost:8000/c2/api/v1 C3_BASE_URL=http://localhost:8000/c3/api/v1`
to mount the C2/C3 stubs (`stubs/app.py` at the repository root) inside the same process. Without the
stubs, `GET /dashboard/overview` answers 502 BAD_GATEWAY as designed; set `OVERVIEW_LOCAL_FALLBACK=1`
to build it from the local `vehicle` and `alert` tables instead (`degraded: true`).

Against MariaDB/MySQL: `DATABASE_URL=mysql+aiomysql://scicp:scicp@127.0.0.1/scicp` (the brief's
`mysql+pymysql://` form is accepted and mapped). The schema is created at startup with
`Base.metadata.create_all` plus the view `v_alert_summary`.

Seed only: `python -m seed.seed` from the repository root with the same environment variables.

Model artifacts: `STORAGE_BACKEND=local` (default) keeps them under `STORAGE_DIR` and serves the
presigned URLs itself (`PUT`/`GET /api/v1/storage/objects/{key}?op&exp&sig`, HMAC-SHA256 with
`JWT_SECRET`, 403 on a bad or expired signature, a `.sha256` sidecar per object);
`STORAGE_BACKEND=s3` uses Amazon S3 (`S3_MODELS_BUCKET`, `S3_REGION`, the instance role or
`S3_ACCESS_KEY`/`S3_SECRET_KEY`). `LocalStorageClient` and `StorageClient` have the same interface.

Model deployment (C4 classifier worker, service token): `GET /internal/models/active` ->
`{modelId, name, version, tag, sha256, sizeBytes, downloadUrl}`; `POST /internal/worker/status`
`{workerId, modelId?, modelVersion, sha256, state: READY|LOADING|FAILED, loadedAt, detail}`.
`GET /models/{id}/deployment` (IOT_ENGINEER, SERVICE_STAFF, SYS_ADMIN) -> `{activeVersion, workers[{workerId,
modelVersion, state, loadedAt, sha256Verified, lastHeartbeatAt, stale}]}`; every report is also
sent to the staff room as the WebSocket message `model.deployed` and kept in the MongoDB
collection `worker_status`.

Demo logins (password `Passw0rd!demo`): anh@example.com (OWNER), maria@bayshuttle.example
(FLEET_MANAGER), rider@example.com (RIDER), iot@scicp.example, staff@scicp.example, admin@scicp.example.

```bash
curl -s -X POST localhost:8000/api/v1/auth/login -H 'content-type: application/json' \
  -d '{"email":"maria@bayshuttle.example","password":"Passw0rd!demo"}'
curl -s localhost:8000/api/v1/dashboard/map -H "Authorization: Bearer $TOKEN"
```

## Test

```bash
cd c1/api
python -m pytest -q                       # SQLite + mock MongoDB
TEST_DATABASE_URL=mysql+aiomysql://scicp:scicp@127.0.0.1/scicp_test python -m pytest -q
```

The suite seeds a fresh database, replaces the C2/C3 clients and the artifact storage with
in-memory fakes, and covers one test per column of the decision tables (BR-02, BR-04, BR-06/07,
BR-08/09, BR-10, BR-11), tenancy, pagination, the vehicle map, the WebSocket fan-out, the signed
local storage routes (on a temporary directory) and the model deployment endpoints.

## Layout

```
app/main.py          create_app(), lifespan (schema, seed, stubs), stubs_mount hook
app/config.py        Settings (pydantic-settings, env names of the brief)
app/errors.py        error envelope and exception handlers
app/db.py            engine, session factory, create_schema, Motor / mongomock
app/models/          SQLAlchemy models: C1 tables (c1.py) and read-only C2/C3 tables (c2c3.py)
app/core/            security (PasswordHasher, TokenService), rbac (RbacGuard, TenantContext),
                     eventbus, audit (AuditLogger), cache, pagination, timeutil
app/repos/           *Repo classes (SQL), AlertSummaryRepo (view), EventQueryRepo (MongoDB)
app/clients/         VehicleClient, AlertClient, StorageClient (S3), LocalStorageClient, MailSender, MqttPublisher
app/services/        AuthService + LoginThrottle, DashboardService, SubscriptionService,
                     ReportService (+ report types), AdminService, ModelRegistryService,
                     DeploymentService (classifier worker status)
app/ws/hub.py        WsHub
app/routers/         one thin router per API group, plus /internal, /storage and /ws
tests/               pytest suite
```
