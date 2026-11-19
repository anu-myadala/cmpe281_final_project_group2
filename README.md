# Smart Car Intelligent Cloud Platform (SCICP)

CMPE 281 (Cloud Technologies), San Jose State University, Fall 2026, Group 2. Instructor: Dr. Jerry Gao.

SCICP monitors autonomous cars through the microphones of an in-car edge device. Each car sends
telemetry and short sound clips; a classifier in the cloud labels every clip (siren, crash, glass
break, scream, honking, traffic, environmental); rules turn the result into alerts; and the
platform notifies owners, fleet managers and the service staff in a live, multi-tenant web app.
All four components below are in this repository and run together (`docker compose up`), on a
laptop or on one AWS EC2 instance.

| Component | Owner | Folder | What it does |
|---|---|---|---|
| C1 Cloud Platform Dashboard and System Admin | Anukrithi Myadala | `c1/` (web app and API), `seed/`, `deploy/` | Login and roles, tenant dashboard with the live vehicle map, subscriptions, reports and statistics, billing, tenants and users, audit log, platform health, the AI model registry (C4 pages), the load-balanced deployment |
| C2 Edge Agent and IoT Device Management | Brandon Rivera Venegas | `c2/` (service, console, edge agent) | Vehicles and devices, provisioning, device config and commands, telemetry and clip ingestion over MQTT and HTTP, the simulated cars |
| C3 Alert Tracking and Monitoring | Ray Zhao | `c3/` | Alert rules, the alert engine, the alert queue and alert page, notifications (email) |
| C4 Sound classifier worker | Ray Zhao | `c3/worker/` | CLAP audio model (zero-shot or with a trained head): classifies each clip, loads the model version that C1's registry activates |

`stubs/` holds the stand-ins for C2 and C3 that C1 was built against before the merge; they are
used only when C1 runs alone (see [Running C1 alone](#running-c1-alone-with-the-stand-ins)).

## Architecture

```mermaid
flowchart LR
  subgraph Cars["Edge (simulated cars, c2/edge)"]
    E[edge agent per car<br/>telemetry 10 s, clip 30-90 s]
  end
  subgraph Web["Browser"]
    W[React web app<br/>c1/web]
  end
  subgraph Cloud["Cloud (docker compose / EC2)"]
    N[nginx :8080 / :80<br/>web app + proxies]
    A[C1 API :8000<br/>auth, dashboard, reports, admin,<br/>model registry, WebSocket]
    C2[C2 service :8002<br/>vehicles, devices, clips]
    C3[C3 service :8003<br/>rules, alerts, notifications]
    C4[C4 worker :8010<br/>CLAP classifier]
    Q[Mosquitto MQTT :1883]
    M[(MySQL 8<br/>shared SQL tables)]
    D[(MongoDB 7<br/>telemetry, audio_events)]
    O[(model artifacts<br/>and clips)]
    E2[MailHog / SES]
  end
  E -->|telemetry, status| Q
  E -->|POST /ingest/clips| C2
  E -->|audio/event| Q
  Q --> C2
  Q -->|audio/event| C4
  C4 -->|GET /internal/clips| C2
  C4 -->|POST /internal/events/:ref/result| C3
  C3 -->|POST /internal/events| A
  C2 -->|device.status| A
  A -->|model.activated| Q
  Q -->|model.activated| C4
  C4 -->|GET /internal/models/active,<br/>POST /internal/worker/status| A
  W -->|/api, /ws, /c2, /c3| N
  N --> A
  N --> C2
  N --> C3
  A -->|GET /vehicles| C2
  A -->|GET /alerts, /alerts/map| C3
  C3 --> E2
  A --> E2
  A --- M
  C2 --- M
  C3 --- M
  A --- D
  C2 --- D
  C4 --- D
  A --- O
  C2 --- O
```

One alert, end to end: a simulated car uploads a 5 s clip to C2 and announces it on MQTT; the C4
worker fetches the clip, classifies it with the active model version and stores the result in
MongoDB; C3 applies the alert rules, writes the alert and emails the subscribers; C3 tells C1, and
C1's WebSocket hub pushes the alert to every open dashboard of that tenant (toast, badge, map pin).
Activating another model version in C1's registry reaches the worker over MQTT; the worker
downloads the file through C1, verifies its SHA-256 and reports back, and the next alerts carry
the new version.

## Repository layout

```
README.md                 this file
docker-compose.yml        base stack: mysql, mongo, mailhog, mosquitto, seed, api, stubs, web
docker-compose.c3.yml     overlay: the real C3            docker-compose.c2.yml   overlay: the real C2 and the simulated cars
docker-compose.c2c3.yml   overlay: nginx with both        docker-compose.c4.yml   overlay: the C4 classifier worker
docker-compose.scale.yml  overlay: two replicas of the API, C2 and C3 behind the nginx load balancer
docker-compose.aws.yml    EC2 overrides (web on :80, no published database ports, optional Amazon S3)
.env.example              every variable with development defaults; COMPOSE_FILE selects the full platform
c1/api/                   C1 FastAPI backend (Python 3.11), pytest suite in tests/
c1/web/                   C1 React 18 + TypeScript + Vite + MUI web app, Playwright tests
c2/                       C2 service (c2_service), console, edge agent (edge/), tests
c3/                       C3 service (c3_service), tests, tools; c3/worker/ is the C4 classifier worker
seed/                     seed.py (shared demo data) and the sound-classifier model versions with their training scripts
stubs/                    C2 and C3 stand-ins for running C1 alone
deploy/                   ec2-bootstrap.sh, reset-demo.sh, show.sh, nginx-scaled.conf, mosquitto.conf, DEMO_SCRIPT.md
tools/                    loadtest.py (load test through the load balancer)
docs/                     BRIEF.md, API_NOTES.md, the design (spec_DESIGN.md, spec_overview, spec_part1..3)
```

## Prerequisites

- Docker Engine 24+ with the compose plugin (`docker compose version` >= 2.24, for the `!override` tag used by the AWS file).
- For the developer mode without Docker: Python 3.11+ (3.13 works), Node 20+, a MySQL 8 or MariaDB 10.11 server with a database `scicp` and user `scicp`/`scicp` (or SQLite, see below). MongoDB is optional: `MONGO_URL=mock` runs an in-process mock.

## Quick start with Docker (the whole platform)

```bash
cp .env.example .env               # COMPOSE_FILE in it selects C1 + C2 + C3 + C4
docker compose up -d --build
```

The first build takes several minutes (Python wheels, npm install, Vite build), and the C4 worker
downloads the CLAP weights once (about 600 MB, kept on the volume `c4-hf-cache`). `seed` runs once
and exits 0; the services wait for it. The simulated cars start sending telemetry at once and a
clip every 30 to 90 s, so the first live alerts arrive after a minute or two. Then:

| What | URL |
|---|---|
| Web app | http://localhost:8080 |
| C2 console (vehicles, devices, provisioning, clips) | http://localhost:8080/c2/console/ |
| API docs (OpenAPI): C1, C2, C3 | http://localhost:8000/api/v1/docs, http://localhost:8012/api/v1/docs, http://localhost:8003/api/v1/docs |
| Health: C1, C4 worker | http://localhost:8000/api/v1/health, http://localhost:8010/healthz (and **Platform health** in the web app as admin) |
| MailHog (invitations, alert emails) | http://localhost:8025 |
| MySQL / MongoDB / MQTT | localhost:3306 (scicp/scicp), localhost:27017, localhost:1883 |

The C4 worker needs about 3 GB of memory on top of the rest (give Docker Desktop 8 GB). On a
smaller machine remove `:docker-compose.c4.yml` from `COMPOSE_FILE`: the stubs container then runs
a simulated worker instead. The sound clips come from `c2/edge/datasets/<class>/*.wav` when that
folder has the course's Smart Car audio clips, otherwise the cars send synthetic sounds.

Useful commands: `docker compose ps`, `docker compose logs -f api c2 c3 c4`,
`docker compose exec mysql mysql -uscicp -pscicp scicp`, `docker compose exec mongo mongosh scicp`,
`bash deploy/show.sh services|tests|audit|models|alerts|logs` (read-only back-end views),
`bash deploy/reset-demo.sh` (fresh demo data, keeps the CLAP weights), `docker compose down -v`
(remove everything, including the databases).

All images are official multi-architecture images (mysql:8.0, mongo:7, eclipse-mosquitto:2,
python:3.11-slim, node:20-alpine, nginx:1.27-alpine), so the stack builds natively on an
Apple-silicon Mac and on an x86 EC2 instance. The one exception is `mailhog/mailhog`, which is
published for amd64 only; Docker Desktop runs it under emulation (`platform: linux/amd64` in the
compose file). Without MailHog set `MAIL_MODE=log` and read the invitations in `docker compose logs api`.

### Model artifacts and deployment

The model artifacts (`models/{modelId}/{version}/model.pt`) are stored by the API itself on the
named volume `objects` (`STORAGE_BACKEND=local`, `STORAGE_DIR=/data/objects`), on the laptop and,
by default, on EC2; set `AWS_STORAGE_BACKEND=s3` on EC2 to use Amazon S3 instead (see below). The interface is the same in both cases: the API
hands out presigned URLs. Locally these are relative, HMAC-signed API paths
(`/api/v1/storage/objects/<key>?op=put|get&exp=&sig=`, signed with `JWT_SECRET`, 15 min), so the
browser uploads and downloads through nginx on whatever host name the page was opened with; each
object gets a `<file>.sha256` sidecar with its digest.

The C4 classifier worker (`c3/worker`; when C1 runs alone, the simulated one in `stubs/worker.py`)
follows the registry. After an activation or a rollback it is notified on MQTT (`scicp/v1/platform/model/activated`, QoS 1,
retained; inline mode: the EventBus topic `model.activated`) and polls C1 every 30 s as a fallback.
It asks `GET /internal/models/active` for the version, its SHA-256 and a presigned download URL,
downloads and verifies the artifact, and reports `POST /internal/worker/status` (LOADING, then READY
or FAILED). The **AI models** page shows this in the Deployment card (live through the WebSocket
message `model.deployed`), and new alerts carry the version the worker has loaded
(`inference.modelVersion`, shown on the alert detail page).

## Scalability and load balancing

`docker-compose.scale.yml` (added last to `COMPOSE_FILE`; the EC2 bootstrap adds it by default) runs
two replicas each of the C1 API, C2 and C3 with nginx as the load balancer (`deploy/nginx-scaled.conf`):

| Concern | How |
|---|---|
| Load balancing | nginx round robin over the replicas of each service; `X-Upstream` in every answer names the replica (`bash deploy/show.sh lb` counts them) |
| Service discovery | Docker's DNS returns every replica of `api`, `c2`, `c3`; nginx re-resolves every 10 s (`resolve`), so `docker compose up -d --scale c2=3` adds a replica without a restart |
| Failover | a replica that fails is skipped for 10 s (`max_fails`), and the request is retried on the next one (`proxy_next_upstream`); in the AWS test all 5,625 requests succeeded while a replica of each service was stopped (`docs/LOADTEST.md`) |
| Shared state | everything durable is in MySQL, MongoDB and the volumes; the API replicas share their live events over MQTT (`EVENT_FANOUT=1`), so every WebSocket sees every alert |
| Message streams | C2's replicas use MQTT shared subscriptions (`$share/c2/...`): the broker gives each telemetry or status message to one replica; the C4 worker supports the same (`C4_SHARE_GROUP`) for a pool of workers |
| Background jobs | C2's sweep, command timeouts and retention run on one replica at a time (MySQL `GET_LOCK`); C1, C2 and C3 create their tables under the same kind of lock, so replicas that start together on an empty database do not collide |

`tools/loadtest.py` measures the platform through the load balancer (requests per second and
latency per endpoint, and which replica served them); `docs/LOADTEST.md` has the numbers measured on EC2.

## Billing

Every tenant pays the monthly fee of its plan plus usage over the plan's allowances, measured from
the platform's own data: vehicles (C2), clips classified (C4's `audio_events`), alert emails sent
(C3's `notification`) and clip storage (C2's `clip`). The **Billing** page shows the tenant's
invoice for the month (to date and projected), the usage against the allowances and the price
list; staff see every tenant's bill and the platform's revenue, and the system administrator edits
the price list (audited). Default prices (per month): BASIC $9.99 (3 vehicles, 10,000 clips, 500
emails), FLEET $199 (10 vehicles, then $12 each; 100,000 clips), ENTERPRISE $1,499 (100 vehicles,
then $9 each; 2,000,000 clips); clips over the allowance $0.30 to $0.50 per 1,000, emails $0.50 to
$1.00 per 100, storage $0.05 to $0.10 per GB-month. API: `docs/API_NOTES.md`.

## Demo accounts

All passwords are `Passw0rd!demo`.

| Email | Role | Tenant | What to look at |
|---|---|---|---|
| anh@example.com | OWNER | 1 Nguyen household (BASIC plan, AV-01) | overview, map, alert 20482, subscriptions, reports |
| maria@bayshuttle.example | FLEET_MANAGER | 2 Bay Shuttle LLC (FLEET plan, AV-10..AV-14) | vehicle map with five cars, statistics, alert rules |
| rider@example.com | RIDER | 1 (active rider of AV-01) | alert monitor restricted to the trip's car |
| iot@scicp.example | IOT_ENGINEER | staff | vehicles, devices, provisioning, models, tenants (no alerts) |
| staff@scicp.example | SERVICE_STAFF | staff | any tenant through the tenant selector, statistics, models |
| admin@scicp.example | SYS_ADMIN | staff | tenants, users and roles, alert types, audit log, platform health, model activation |

Seed data: 2 tenants, 6 users, 6 vehicles (AV-13 went offline 14 min ago, AV-14 was never
provisioned), 41 alerts over 14 days (20482 is the NEW ACCIDENT on AV-01), ~400 audio events,
10 min of telemetry per online car around SJSU, model `sound-classifier` 1.1.0 (active, CLAP
zero-shot, macro-F1 0.68) and 1.2.0 (trained head, 0.99), 15 feedback labels; `seed/models/README.md`
explains how the versions were trained and scored. `deploy/DEMO_SCRIPT.md` is the demo script.

## Developer mode without Docker (C1)

One process runs C1 with everything it needs: the API with the C2/C3 stubs mounted inline, the seed at
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

Each component has its own suite; none needs Docker or a running platform (SQLite, a MongoDB mock
and fakes for the neighbors).

```bash
cd c1/api && python -m pytest -q          # C1: 108 tests (decision tables of BR-02/04/06/07/08/09/10/11, tenancy,
                                          #     WebSocket, storage, model deployment, audit log, health, billing, event fan-out)
cd c2 && python -m pytest -q              # C2: 28 tests (needs c2/requirements.txt, c2/edge/requirements.txt, c1/api/requirements.txt)
cd c3 && python -m pytest -q              # C3: 405 tests (needs c3/requirements.txt and c1/api/requirements.txt)
cd c3/worker && python -m pytest -q       # C4: 68 tests, fake encoder, no model download (see c3/worker/requirements-dev.txt)
cd c1/web && npm run typecheck && npm run build
cd c1/web && npx playwright test          # C1 smoke test (starts the API and the Vite server itself)
cd c1/web && E2E_BASE_URL=http://localhost:8080 npx playwright test   # against the running platform
```

With `E2E_BASE_URL` the configuration starts no server and also runs `tests/stack.spec.ts`: the
owner flow, Maria's moving cars and a live acknowledge seen from a second session, and the admin
flow (tenant, invitation found in MailHog's API on :8025, model upload through the UI, activation
with the BR-10 dialog, the Deployment card going LOADING -> READY, rollback). On EC2,
`bash deploy/show.sh tests -q` runs the C1 suite in a one-off container.

## AWS EC2 deployment

1. Launch Ubuntu 24.04 LTS (x86_64) with 8 GB of memory for the whole platform (`t3.large`, or `m7i-flex.large` on the AWS Free plan; the C4 worker alone needs about 3 GB) and a 30 GB gp3 disk (`t3.small` with 20 GB is enough for C1 with the stand-ins: the bootstrap adds a 4 GB swap file for the image builds), a key pair, and a security group with 22 from your IP, 80 from anywhere, and 8025 (MailHog) from your IP. Nothing else is needed for the default setup: the model artifacts stay on the instance disk and the invitation emails go to MailHog. Optional Amazon S3 for the artifacts: set `AWS_STORAGE_BACKEND=s3` in `.env`, attach an instance role with `s3:*` on `arn:aws:s3:::scicp-models*` (or set `AWS_S3_ACCESS_KEY` / `AWS_S3_SECRET_KEY`), and create the private bucket `scicp-models` in `us-west-2` with a CORS rule allowing `PUT` and `GET` from `http://<public-host>`; the browser and the classifier worker then use presigned S3 URLs.
2. SSH in and run the bootstrap (installs Docker, clones, writes `.env` with fresh secrets and the public hostname, starts the stack):
   ```bash
   curl -fsSL https://raw.githubusercontent.com/anu-myadala/cmpe281_final_project_group2/main/deploy/ec2-bootstrap.sh | bash -s -- https://github.com/anu-myadala/cmpe281_final_project_group2.git
   ```
   or clone first and run `bash deploy/ec2-bootstrap.sh`. It ends with the URLs; the web app is `http://<public-host>/`.
3. Operate: `sudo docker compose ps|logs -f|down` in `~/scicp`. The bootstrap writes the compose files to `.env` as `COMPOSE_FILE`: `docker-compose.yml` and `docker-compose.aws.yml`, plus the C3, C2 and C4 overlays (`SCICP_STUBS_ONLY=1` keeps the stand-ins). `bash deploy/show.sh services|tests|audit|models|alerts|logs` prints the back-end views used in the demo (containers, the C1 unit tests in a one-off container, the audit log, the model versions, the newest alerts), and `bash deploy/reset-demo.sh` restores the fresh demo data without downloading the CLAP weights again. After a stop/start of the instance the public DNS changes: run the bootstrap again (it rewrites `PUBLIC_HOST`, `C3_PUBLIC_BASE_URL` and `CORS_ORIGINS`). Mail goes to MailHog (`http://<public-host>:8025`); `AWS_MAIL_MODE=log` writes it to the API log instead and `AWS_MAIL_MODE=ses` sends it through SES with a verified `MAIL_FROM`. HTTPS is out of scope for the course demo; put an ALB or Caddy in front for a real deployment and set `COOKIE_SECURE=true`.

## Running C1 alone with the stand-ins

C1 only knows the base URLs and the contract in `docs/API_NOTES.md`, so it also runs without the
other components: comment out `COMPOSE_FILE` in `.env` (or run `docker compose -f docker-compose.yml up -d --build`).
The `stubs` container then serves C2 and C3 from the same shared tables, generates telemetry and
alerts, and simulates the C4 worker; nginx sends `/c2/` and `/c3/` to it. On EC2,
`SCICP_STUBS_ONLY=1 bash deploy/ec2-bootstrap.sh` does the same.

How the real components plug in, without changing a shared file:

- `C2_BASE_URL` / `C3_BASE_URL` point the API's `VehicleClient` and `AlertClient` at C2 and C3; the
  overlays `docker-compose.c2.yml`, `docker-compose.c3.yml` and `docker-compose.c2c3.yml` set them and
  swap the nginx file so that the browser's `/c2/` and `/c3/` paths reach the real services.
- C2 and C3 accept the user JWT (`JWT_SECRET`) and the service token (`SERVICE_TOKEN_SECRET`), and
  publish `alert.created`, `alert.updated` and `device.status` with `POST /internal/events`.
- The shared tables `vehicle`, `vehicle_rider`, `alert`, `alert_action` and the view `v_alert_summary`
  keep the columns of Parts II and III of the design, so the dashboard reads the same data either way.
- `docker-compose.c4.yml` adds the real classifier worker and turns off the simulated one.

## Documentation

`docs/BRIEF.md` (implementation brief with the late changes), `docs/API_NOTES.md` (the exact
request and response shapes, the WebSocket protocol and the internal endpoints),
`docs/spec_DESIGN.md` and `docs/spec_part1_c1.txt` (Deliverable #2 design of C1),
`docs/spec_overview.txt`, `docs/spec_part2_c2.txt`, `docs/spec_part3_c3.txt` (the design of the
other components), `c1/api/README.md`, `c1/web/README.md`, `c2/README.md` (with the console and
the live laptop car), `c3/README.md` (with the C4 worker), `seed/models/README.md` (the model
versions), `deploy/DEMO_SCRIPT.md` (the demo).
