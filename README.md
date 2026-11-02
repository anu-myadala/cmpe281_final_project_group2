# Smart Car Intelligent Cloud Platform (SCICP)

CMPE 281 (Cloud Technologies), San Jose State University, Fall 2026, Group 2. Instructor: Dr. Jerry Gao.

| Component | Owner | Where |
|---|---|---|
| C1 Cloud Platform Dashboard and System Admin (with the C4 model registry pages) | Anukrithi Myadala | `c1/`, `seed/`, `stubs/`, `deploy/` (this repository state) |
| C2 Vehicle, Device and Data Management | Brandon Rivera Venegas | joins this monorepo as `c2/` |
| C3 Sound Intelligence and Alerting | Ray Zhao | joins this monorepo as `c3/` |

SCICP monitors autonomous cars through the microphones of an in-car edge device: audio events
are classified in the cloud (siren, crash, glass break, scream, honking, traffic,
environmental), rules turn them into alerts, and the platform notifies owners, fleet managers
and the service staff. Component C1 is the multi-tenant web platform: authentication and
roles, the tenant dashboard with the live vehicle map, intelligence-service subscriptions,
reports and statistics, tenant and user administration, and the AI model registry.

The design is fixed by Deliverable #2 (`docs/spec_DESIGN.md`, `docs/spec_part1_c1.txt`);
`docs/BRIEF.md` lists the late changes and `docs/API_NOTES.md` what the running API returns.

## Architecture

```mermaid
flowchart LR
  subgraph Browser
    W[React web app<br/>c1/web]
  end
  subgraph Platform
    N[nginx :8080<br/>static + proxies]
    A[C1 API, FastAPI :8000<br/>auth, dashboard, subscriptions,<br/>reports, admin, models, /ws]
    S2[C2 vehicles / devices / telemetry<br/>stub :8002]
    S3[C3 alerts / rules<br/>stub :8003]
    G[demo event generator<br/>+ C4 classifier worker]
  end
  subgraph Data
    M[(MySQL 8)]
    D[(MongoDB 7<br/>audio_events,<br/>vehicle_telemetry)]
    O[(model artifacts<br/>local volume or S3 scicp-models)]
    Q[Mosquitto MQTT]
    E[MailHog / SES]
  end
  W -->|/api, /ws, /c2, /c3| N
  N --> A
  N --> S2
  N --> S3
  A -->|GET /vehicles, /devices| S2
  A -->|GET /alerts, /alerts/map| S3
  S3 -->|POST /internal/events, feedback labels| A
  G --> S2
  G --> S3
  A --- M
  A --- D
  A --- O
  A -->|model.activated| Q
  Q -->|model.activated| G
  G -->|GET /internal/models/active,<br/>POST /internal/worker/status| A
  A -->|invitations| E
  S2 --- M
  S2 --- D
  S3 --- M
  S3 --- D
```

Inside the API the names follow the design: `AuthService`, `LoginThrottle`, `TokenService`,
`RbacGuard`, `TenantContext`, `DashboardService`, `SubscriptionService`, `ReportService`,
`AdminService`, `ModelRegistryService`, `AuditLogger`, `WsHub`, `EventBus`, `MailSender`,
`StorageClient`, `VehicleClient`, `AlertClient` and the `*Repo` repositories. Live updates
travel EventBus -> `WsHub` -> WebSocket rooms (`tenant:{id}` and `staff`); the other
components reach the bus through `POST /internal/events` with a service token.

## Repository layout

```
README.md                 this file
docker-compose.yml        local stack: mysql, mongo, mailhog, mosquitto, seed, api, stubs, web (+ volume objects)
docker-compose.aws.yml    EC2 overrides (Amazon S3 for the artifacts, no MailHog, web on :80)
.env.example              every variable with development defaults (copy to .env)
c1/api/                   FastAPI backend (Python 3.11), package `app`, pytest suite in tests/
c1/web/                   React 18 + TypeScript + Vite + MUI + Recharts + Leaflet + TanStack Query, Playwright smoke test
seed/                     seed.py: idempotent MySQL + MongoDB (+ model artifacts) seed with the Deliverable #2 sample data
stubs/                    C2 and C3 stand-ins (FastAPI) + demo event generator + simulated C4 classifier worker; inline or standalone
deploy/                   ec2-bootstrap.sh, mosquitto.conf, DEMO_SCRIPT.md
docs/                     BRIEF.md, API_NOTES.md, the design (spec_DESIGN.md, spec_part1..3, spec_overview)
```

## Prerequisites

- Docker Engine 24+ with the compose plugin (`docker compose version` >= 2.24, for the `!override` tag used by the AWS file).
- For the developer mode without Docker: Python 3.11+ (3.13 works), Node 20+, a MySQL 8 or MariaDB 10.11 server with a database `scicp` and user `scicp`/`scicp` (or SQLite, see below). MongoDB is optional: `MONGO_URL=mock` runs an in-process mock.

## Quick start with Docker

```bash
cp .env.example .env
docker compose up --build          # add -d to run in the background
```

The first build takes a few minutes (Python wheels, npm install, Vite build). `seed` runs once
and exits 0; `api` waits for it. Then:

| What | URL |
|---|---|
| Web app | http://localhost:8080 |
| API docs (OpenAPI) | http://localhost:8000/api/v1/docs |
| API health | http://localhost:8000/api/v1/health |
| C2 / C3 stub docs | http://localhost:8002/api/v1/docs, http://localhost:8003/api/v1/docs |
| MailHog (invitation emails) | http://localhost:8025 |
| MySQL / MongoDB / MQTT | localhost:3306 (scicp/scicp), localhost:27017, localhost:1883 |

Useful commands: `docker compose logs -f api stubs`, `docker compose exec mysql mysql -uscicp -pscicp scicp`,
`docker compose exec mongo mongosh scicp`, `docker compose run --rm seed` (re-seed; existing rows are kept),
`docker compose down -v` (fresh databases and artifacts).

All images are official multi-architecture images (mysql:8.0, mongo:7, eclipse-mosquitto:2,
python:3.11-slim, node:20-alpine, nginx:1.27-alpine), so the stack builds natively on an
Apple-silicon Mac and on an x86 EC2 instance. The one exception is `mailhog/mailhog`, which is
published for amd64 only; Docker Desktop runs it under emulation (`platform: linux/amd64` in the
compose file). Without MailHog set `MAIL_MODE=log` and read the invitations in `docker compose logs api`.

### Model artifacts and deployment

The model artifacts (`models/{modelId}/{version}/model.pt`) are stored by the API itself on the
named volume `objects` (`STORAGE_BACKEND=local`, `STORAGE_DIR=/data/objects`); on EC2 they go to
Amazon S3 (`STORAGE_BACKEND=s3`, see below). The interface is the same in both cases: the API
hands out presigned URLs. Locally these are relative, HMAC-signed API paths
(`/api/v1/storage/objects/<key>?op=put|get&exp=&sig=`, signed with `JWT_SECRET`, 15 min), so the
browser uploads and downloads through nginx on whatever host name the page was opened with; each
object gets a `<file>.sha256` sidecar with its digest.

The stubs container also runs a simulated C4 classifier worker (`stubs/worker.py`). After an
activation or a rollback it is notified on MQTT (`scicp/v1/platform/model/activated`, QoS 1,
retained; inline mode: the EventBus topic `model.activated`) and polls C1 every 30 s as a fallback.
It asks `GET /internal/models/active` for the version, its SHA-256 and a presigned download URL,
downloads and verifies the artifact, and reports `POST /internal/worker/status` (LOADING, then READY
or FAILED). The **AI models** page shows this in the Deployment card (live through the WebSocket
message `model.deployed`), and new alerts from the demo generator carry the version the worker has
loaded (`inference.modelVersion`, shown on the alert detail page).

## Demo accounts

All passwords are `Passw0rd!demo`.

| Email | Role | Tenant | What to look at |
|---|---|---|---|
| anh@example.com | OWNER | 1 Nguyen household (BASIC plan, AV-01) | overview, map, alert 20482, subscriptions, reports |
| maria@bayshuttle.example | FLEET_MANAGER | 2 Bay Shuttle LLC (FLEET plan, AV-10..AV-14) | vehicle map with five cars, statistics, alert rules |
| rider@example.com | RIDER | 1 (active rider of AV-01) | alert monitor restricted to the trip's car |
| iot@scicp.example | IOT_ENGINEER | staff | vehicles, devices, provisioning, models, tenants (no alerts) |
| staff@scicp.example | SERVICE_STAFF | staff | any tenant through the tenant selector, statistics, models |
| admin@scicp.example | SYS_ADMIN | staff | tenants, users and roles, alert types, model activation |

Seed data: 2 tenants, 6 users, 6 vehicles (AV-13 went offline 14 min ago, AV-14 was never
provisioned), 41 alerts over 14 days (20482 is the NEW ACCIDENT on AV-01), ~400 audio events,
10 min of telemetry per online car around SJSU, model `sound-classifier` 1.1.0 (active) and
1.2.0, 15 feedback labels. `deploy/DEMO_SCRIPT.md` is the minute-by-minute script.

## Developer mode without Docker

One process is the whole demo: the API with the C2/C3 stubs mounted inline, the seed at
startup and the in-process MongoDB mock.

```bash
cd c1/api
pip install -r requirements.txt
DATABASE_URL=mysql+pymysql://scicp:scicp@127.0.0.1/scicp MONGO_URL=mock SEED_ON_START=1 STUBS_INLINE=1 \
  STORAGE_DIR=./objects C1_BASE_URL=http://127.0.0.1:8000/api/v1 \
  C2_BASE_URL=http://127.0.0.1:8000/c2/api/v1 C3_BASE_URL=http://127.0.0.1:8000/c3/api/v1 \
  python3 -m uvicorn app.main:app --port 8000 --reload
# in another terminal
cd c1/web && npm ci && npm run dev        # http://localhost:5173, proxies /api, /ws, /c2, /c3 to :8000
```

Without a MySQL server use `DATABASE_URL=sqlite+aiosqlite:///./dev.db`. The generator
publishes straight on the EventBus in this mode, and the classifier worker listens to
`model.activated` on it (it reaches the API over HTTP at `C1_BASE_URL`); `DEMO_ALERT_PERIOD_S=30` makes alerts arrive
faster while developing. The stubs can also run on their own: `python -m stubs.standalone`
(ports 8002/8003, `C1_BASE_URL=http://127.0.0.1:8000/api/v1`), with `STUBS_INLINE=0` and the
default `C2_BASE_URL`/`C3_BASE_URL` on the API.

## Tests

```bash
cd c1/api && python -m pytest -q                 # 90 tests: BR-02/04/06/07/08/09/10/11 decision tables, tenancy, WebSocket,
                                                 # signed local storage, model deployment endpoints
cd c1/web && npm run typecheck && npm run build  # tsc --noEmit, Vite production build
cd c1/web && npx playwright test                 # smoke: login -> overview -> map -> subscriptions -> reports -> admin -> models
cd c1/web && E2E_BASE_URL=http://localhost:8080 npx playwright test   # against the running docker compose stack
```

With `E2E_BASE_URL` the configuration starts no server and also runs `tests/stack.spec.ts`: the
owner flow, Maria's moving cars and a live acknowledge seen from a second session, and the admin
flow (tenant, invitation found in MailHog's API on :8025, model upload through the UI, activation
with the BR-10 dialog, the Deployment card going LOADING -> READY, rollback).

The backend tests use SQLite and the MongoDB mock by default (`TEST_DATABASE_URL` switches to
MySQL/MariaDB). The Playwright configuration starts the API (SQLite, seeded) and the Vite dev
server itself, or reuses servers already running on :8000 and :5173, so it also works against
the inline-stubs process above.

## AWS EC2 deployment

1. Launch Ubuntu 24.04 LTS, `t3.small` or larger (the build needs ~2 GB RAM; add a 2 GB swap file on `t3.small`), 20 GB disk. Security group: 22 from your IP, 80 from anywhere. Attach an instance role with `s3:*` on `arn:aws:s3:::scicp-models*` (or put keys in `.env` as `AWS_S3_ACCESS_KEY` / `AWS_S3_SECRET_KEY`). Create the bucket `scicp-models` in `us-west-2` (private; the API only hands out presigned URLs) with a CORS rule allowing `PUT` and `GET` from `http://<public-host>`. `docker-compose.aws.yml` sets `STORAGE_BACKEND=s3` for the API and the seed (no endpoint URL: the regional S3 endpoint), so the seed uploads the two dummy artifacts to the bucket and the browser and the classifier worker use presigned S3 URLs.
2. SSH in and run the bootstrap (installs Docker, clones, writes `.env` with fresh secrets and the public hostname, starts the stack):
   ```bash
   curl -fsSL https://raw.githubusercontent.com/<org>/<repo>/main/deploy/ec2-bootstrap.sh | bash -s -- https://github.com/<org>/<repo>.git
   ```
   or clone first and run `bash deploy/ec2-bootstrap.sh`. It ends with the URLs; the web app is `http://<public-host>/`.
3. Operate: `docker compose -f docker-compose.yml -f docker-compose.aws.yml ps|logs -f|down`. After a stop/start of the instance the public DNS changes: run the bootstrap again (it rewrites `PUBLIC_HOST` and `CORS_ORIGINS`). Mail goes to the API log unless `AWS_MAIL_MODE=ses` with a verified `MAIL_FROM`. HTTPS is out of scope for the course demo; put an ALB or Caddy in front for a real deployment and set `COOKIE_SECURE=true`.

## Replacing the stubs by the real C2 and C3

C1 only knows the base URLs and the contract in `docs/API_NOTES.md`:

- `C2_BASE_URL` / `C3_BASE_URL` in `.env` point the API's `VehicleClient` and `AlertClient` at the real services; the nginx upstreams `c2_stub` and `c3_stub` in `c1/web/nginx/default.conf` (same-origin `/c2/` and `/c3/` paths for the browser) point at them too.
- C2 and C3 accept the user JWT (`JWT_SECRET`) and the service token (`SERVICE_TOKEN_SECRET`), and publish `alert.created`, `alert.updated` and `device.status` with `POST /internal/events`.
- The shared tables `vehicle`, `vehicle_rider`, `alert`, `alert_action` and the view `v_alert_summary` keep the columns of Parts II and III; the stubs read exactly those, so the dashboard does not change.
- Remove the `stubs` service from `docker-compose.yml` (or set `DEMO_ENABLED=0` to keep only the REST stand-ins), add the `c2`/`c3` services, and run the seed once.

## Documentation

`docs/BRIEF.md` (implementation brief with the late changes), `docs/API_NOTES.md` (the exact
request and response shapes, the WebSocket protocol and the internal endpoints),
`docs/spec_DESIGN.md` and `docs/spec_part1_c1.txt` (Deliverable #2 design of C1),
`docs/spec_overview.txt`, `docs/spec_part2_c2.txt`, `docs/spec_part3_c3.txt` (interfaces of the
other components), `c1/api/README.md` and `c1/web/README.md` (component notes),
`deploy/DEMO_SCRIPT.md` (the demo).
