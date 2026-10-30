# SCICP C1 demo script (Deliverable #3, Nov 11, 2026)

Two deliverables share this script: the **live demo** (UI and backend, 3 points) and the
**recorded scenario demo** (2 points). Both run against the same stack; the recorded version
simply follows Part B without the backend detours.

## A. Preparation (the day before and 15 minutes before)

1. Start the stack and the seed.
   - Laptop: `cp .env.example .env && docker compose up --build -d` (first build ~5 min).
   - EC2: `bash deploy/ec2-bootstrap.sh` (already done; `docker compose -f docker-compose.yml -f docker-compose.aws.yml ps` must show every service `running`/`healthy`, `seed` and `minio-init` `exited (0)`).
   - Developer mode (one process, no Docker) if the stack is unavailable:
     ```
     cd c1/api && DATABASE_URL=mysql+pymysql://scicp:scicp@127.0.0.1/scicp MONGO_URL=mock SEED_ON_START=1 \
       STUBS_INLINE=1 C2_BASE_URL=http://127.0.0.1:8000/c2/api/v1 C3_BASE_URL=http://127.0.0.1:8000/c3/api/v1 \
       DEMO_ALERT_PERIOD_S=90 python3 -m uvicorn app.main:app --port 8000
     cd c1/web && npm run dev          # http://localhost:5173
     ```
2. Reset the demo alert if it was touched during a rehearsal (the seed never overwrites an
   existing row):
   ```sql
   -- docker compose exec mysql mysql -uscicp -pscicp scicp
   UPDATE alert SET status='NEW', acknowledged_by=NULL, acknowledged_at=NULL, closed_by=NULL, closed_at=NULL,
          updated_at=created_at WHERE alert_id=20482;
   DELETE FROM alert_action WHERE alert_id=20482 AND action<>'CREATE';
   DELETE FROM feedback_label WHERE event_ref='6729b1c4e1f0a2d3c4b5a697';
   ```
   If the model registry was changed in a rehearsal (1.2.0 active), roll back from the UI or
   run `docker compose down -v && docker compose up -d` for a clean seed (takes ~2 min).
3. Open these tabs in the browser (laptop URLs; on EC2 replace `localhost:8080` with the public host and `localhost:8000` with the SSH tunnel):
   - T1 web app `http://localhost:8080/login`
   - T2 API docs `http://localhost:8000/api/v1/docs`
   - T3 MailHog `http://localhost:8025`
   - T4 MinIO console `http://localhost:9001` (minioadmin / minioadmin), bucket `scicp-models`
4. Open two terminals:
   - TA: `docker compose logs -f api stubs` (shows uvicorn request lines, `scicp.demo` generator lines, `scicp.ws` connections, `scicp.mail`).
   - TB: `docker compose exec mysql mysql -uscicp -pscicp scicp` and, in a second pane, `docker compose exec mongo mongosh scicp`.
5. Accounts (password `Passw0rd!demo` for all):

   | Role | Email | Tenant |
   |---|---|---|
   | OWNER | anh@example.com | 1 Nguyen household (BASIC, AV-01) |
   | FLEET_MANAGER | maria@bayshuttle.example | 2 Bay Shuttle LLC (FLEET, AV-10..AV-14) |
   | RIDER | rider@example.com | 1 (AV-01 only) |
   | IOT_ENGINEER | iot@scicp.example | staff |
   | SERVICE_STAFF | staff@scicp.example | staff |
   | SYS_ADMIN | admin@scicp.example | staff |

6. Make sure the demo generator is running (TA shows `demo generator: 4 cars (AV-01, AV-10, AV-11, AV-12)` and then one `demo: alert ...` line every ~2.5 min). To make alerts arrive faster during the demo set `DEMO_ALERT_PERIOD_S=90` in `.env` and `docker compose up -d stubs`.

## B. Live demo, minute by minute (target 14 minutes)

Timings are cumulative. Each step says what to show in the UI and what to point at in the backend.

### 0:00 Login as the owner (BR-02, JWT)
- T1: log in as `anh@example.com`. Mention: argon2id password check, 15 min access JWT, httpOnly refresh cookie, lockout after 5 failures (BR-02).
- TA: the `POST /api/v1/auth/login 200` line, then `GET /auth/me`, `GET /dashboard/overview`, `GET /dashboard/map`, the `ws connected user=1001 rooms=['tenant:1']` line.
- T2 (optional, 10 s): show the OpenAPI page: auth, dashboard, subscriptions, reports, admin, models, internal.

### 1:00 Overview with the map (C1 aggregates C2 and C3)
- T1 `/overview`: KPI row (1 vehicle, 1 online, open alerts, alerts 24 h, events 24 h), the vehicle card AV-01 with its device summary (3 devices online), the map card with the green AV-01 marker moving every 10 s, the recent alerts table with **20482 ACCIDENT CRITICAL NEW crash 0.91**.
- Say: the vehicle cards come from C2 `GET /vehicles`, the alerts from C3 `GET /alerts?status=OPEN`, the positions from the `vehicle_telemetry` collection, the pins from C3 `GET /alerts/map`; results are cached 30 s / 10 s, and if a neighbor is down the page shows the last cached values with a "degraded" badge.
- TA: the two stub lines `GET /c2/api/v1/vehicles?tenantId=1` and `GET /c3/api/v1/alerts?status=OPEN...` (or `stubs` container lines on :8002/:8003) right after the overview request.

### 2:30 Alert 20482: acknowledge on the alert page (C3 page hosted in the C1 shell)
- T1: click the row 20482 -> `/alerts/20482`: type, severity, top classes from the audio event (`crash 0.91, glass_break, traffic`), the model version `sound-classifier-1.1.0`, the rule (rule 2, crash while MOVING, min 0.70), the notification list.
- Click **Acknowledge**. The toast "Alert 20482 acknowledged" appears, the status chip turns ACKNOWLEDGED, the Resolve / False alarm buttons appear.
- TB (MySQL): `SELECT alert_id, status, acknowledged_by, acknowledged_at FROM alert WHERE alert_id=20482;` and `SELECT action, from_status, to_status, user_id, ts FROM alert_action WHERE alert_id=20482;` show the new row by user 1001.
- TA: `POST /c3/api/v1/alerts/20482/actions 200` followed by the WebSocket fan-out; in the second browser tab (or the overview in another window) the row updated without a reload -> the `alert.updated {alertId, status, by: "Anh Nguyen"}` message.
- Mention: a second Acknowledge answers **409 CONFLICT BR-C3-07** (the state machine), and Resolve / False alarm also post a feedback label to C1 (`feedback_label`, BR-11) that the model registry exports for retraining.

### 4:30 Subscriptions (BR-04: validated, audited, one transaction)
- T1 `/subscriptions`: the AV-01 card with the six intelligence services (PASSENGER and VEHICLE_HEALTH marked design only, not available), channels allowed by the BASIC plan (DASHBOARD always on, EMAIL; SMS and PUSH disabled because of the plan).
- Trigger the error: on SECURITY set only **Quiet from** = 22:00 (leave "to" empty), Save -> the inline **BR-04** message "Quiet hours need both times and they must differ"; nothing is written (TB: `SELECT service_code, enabled, channels, quiet_from, quiet_to FROM subscription WHERE vehicle_id=1;` unchanged).
- Fix it: **Quiet to** = 06:00, Save -> toast "Settings saved". TB: the row now has 22:00:00 / 06:00:00 and `SELECT action, entity, entity_id, ts FROM audit_log ORDER BY audit_id DESC LIMIT 3;` shows `SUBSCRIPTION_SAVE` with the before/after JSON (BR-12).
- Mention: C3 reads this row through `GET /internal/subscriptions` when it creates the next alert (BR-05 quiet hours and channels).

### 6:00 Fleet manager: the vehicle map (the demo's centerpiece)
- Log out (user menu), log in as `maria@bayshuttle.example`. Overview shows 5 vehicles, 3 online (AV-10, AV-11, AV-12), AV-13 OFFLINE (telemetry stopped 14 min ago), AV-14 REGISTERED with no data.
- Menu **Vehicle map** (`/map`): green markers moving every 10 s with heading arrows, the amber AV-13 marker, the gray AV-14 entry in the car list without a position, severity-colored pins of the open alerts.
- Click **AV-11** in the car list: the detail panel (position, heading, speed, drive mode, sample age, open alerts) and the **trail** polyline of the last 60 samples (C2 `GET /vehicles/AV-11/telemetry?limit=60`). Toggle **Follow**: the map re-centers on each refresh. Buttons: Open vehicle / Alerts of this car / Subscriptions.
- Wait for the generator (watch TA for `demo: alert ... -> alert.created`): a **new pin appears at once** on the car's position and the alert row appears highlighted in the overview and the alert monitor, without a reload. If the next alert is more than a minute away, use the fallback below.
- TB (mongosh): `db.vehicle_telemetry.find({"meta.vehicleId":"AV-11"}).sort({ts:-1}).limit(1).pretty()` shows the document shape (`ts, meta, speedKph, driveMode, ignition, location{Point}, network, agent, devices`); `db.audio_events.find({"decision.alert":true}).sort({ts:-1}).limit(1).pretty()` shows the event of the alert that just arrived (topK, modelVersion, location, decision.alertId).
- Also mention the `device.status` message: a microphone goes FAULT and back ONLINE every minute or so (TA `demo: device dev-1112 on AV-11 FAULT (CLIPPING)`), which recolors the marker and the device summary on the vehicle page.

Fallback when no alert is due: in a terminal, publish one through the internal endpoint (this is exactly what C3 does):
```
TOKEN=$(docker compose exec -T api python -c "from app.config import Settings; from app.core.security import TokenService; print(TokenService(Settings()).issue_service_token())")
curl -s -X POST http://localhost:8000/api/v1/internal/events -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"topic":"alert.created","tenantId":2,"payload":{"alert":{"alertId":99901,"vehicleId":"AV-11","type":"EMERGENCY","severity":"HIGH","status":"NEW","topClass":"siren","confidence":0.88,"createdAt":"2026-11-11T18:00:00Z","lat":37.3390,"lon":-121.8850}}}'
```

### 9:00 Service staff: statistics and CSV (BR-08)
- Log in as `staff@scicp.example`, choose **Bay Shuttle LLC** in the tenant selector of the top bar (staff pass `tenantId`; tenant users never can).
- Menu **Statistics**: last 7 days (daily buckets), alerts by type per day, events by class (traffic and environmental dominate), device status, false-alarm rate, mean time to acknowledge. Change the range to 2 days -> hourly buckets. Set a range over 92 days -> **422 BUSINESS_RULE BR-08** shown inline.
- **Export CSV** (Statistics or Reports -> `alerts-by-day`, `format=csv`): the file downloads; open it to show the header row.
- TA: `GET /api/v1/dashboard/stats?from=...&to=...&tenantId=2 200` and `GET /api/v1/reports/alerts-by-day?...&format=csv`.

### 10:30 Administrator: tenant and user (BR-06, BR-07, MailHog)
- Log in as `admin@scicp.example`. Menu **Tenants** -> **New tenant**: name "Demo Rides LLC", type FLEET, plan FLEET (max 50), time zone America/Los_Angeles -> created with status ACTIVE and code; the table shows 3 tenants. Mention BR-06: the vehicle limit follows the plan and a downgrade below the vehicles in use is refused.
- Tab **Users** -> **Invite user**: email `ops@demorides.example`, name "Ops Demo", role FLEET_MANAGER, tenant Demo Rides LLC -> 201. Mention BR-07: a staff role with a tenant (or a tenant role without one) is refused with 400.
- T3 MailHog: the invitation email "Your SCICP account" with the temporary password. Optional: log in with it in a private window to show the forced password change.
- TB: `SELECT user_id, email, role, tenant_id, must_change_password FROM app_user ORDER BY user_id DESC LIMIT 2;` and the `TENANT_CREATE` and `USER_CREATE` rows in `audit_log`.

### 12:00 Model registry: activation with the BR-10 gate, rollback
- Menu **AI models**: `sound-classifier`, versions 1.1.0 ACTIVE (macro-F1 0.84, accuracy 0.88) and 1.2.0 REGISTERED (0.86 / 0.89), per-class metrics table for the 7 classes (siren, crash, glass_break, scream, honking, traffic, environmental).
- Select 1.2.0 -> **Set active**: the confirm dialog shows "1.1.0 (macro-F1 0.84) -> 1.2.0 (macro-F1 0.86)". Confirm. 1.2.0 becomes ACTIVE, 1.1.0 INACTIVE; the staff room receives `model.activated {modelId, version}`.
- Say what BR-10 checks in one transaction: the artifact exists in the bucket with the recorded sha256 and size, the macro-F1 is not lower than the active version's (a lower one needs `force` with a note), the previous version is deactivated, the activation is audited, and the event goes to the EventBus and to MQTT `scicp/v1/platform/model/activated` (retained) for the C4 worker. T4: the bucket `scicp-models/models/1/1.2.0/model.pt`.
- **Rollback** -> 1.1.0 active again (`MODEL_ROLLBACK` audit row, another `model.activated`).
- TB: `SELECT version, status, activated_at FROM model_version;` and `SELECT action, entity, entity_id, ts FROM audit_log ORDER BY audit_id DESC LIMIT 5;`.
- Optional (30 s): **Upload new version** 1.3.0 with a small file: presigned PUT from the browser straight to MinIO, then `complete` verifies the sha256 (a wrong hash is refused, BR-10) and the version becomes REGISTERED.

### 13:30 Close: tests and docs
- Terminal: `cd c1/api && python -m pytest -q` -> 78 passed (decision tables BR-02/04/06/07/08/09/10/11, tenancy 404/403, WebSocket fan-out). `cd c1/web && npm run typecheck && npm run build` and `npx playwright test` (2 passed).
- T2: the OpenAPI page once more; mention `docs/` (design, brief, API notes) and the README for the EC2 deployment.

## C. Recorded scenario demo (2 points, ~6 minutes)

Record Part B without the terminals, in this order, with a short voice-over per step:
1. Login as Anh -> overview with the moving map -> alert 20482 -> Acknowledge on the alert page (show the toast and the status chip).
2. Subscriptions: the BR-04 quiet-hours error, then the successful save.
3. Logout, login as Maria -> Vehicle map: moving cars, select AV-11 (trail), Follow on, wait for or inject one new alert pin.
4. Staff: statistics for Bay Shuttle and the CSV download.
5. Admin: create the tenant, invite the user, show MailHog.
6. AI models: activate 1.2.0 (dialog with both macro-F1 values), rollback.
7. Last frame: the pytest summary and the Playwright summary.

## D. Things that can go wrong

| Symptom | Cause / fix |
|---|---|
| Overview shows "degraded" or 502 | the stubs container is not up: `docker compose ps stubs`, `docker compose logs stubs`; the API caches the last good answer for 30 s |
| Markers do not move | the generator stopped (`docker compose logs stubs | grep demo`), or `DEMO_ENABLED=0`; restart `docker compose restart stubs` |
| Alert 20482 is already ACKNOWLEDGED | rehearsal leftover: run the SQL of step A.2 |
| No pin on a new alert | the browser tab lost the WebSocket (reconnects within 10 s with polling fallback); reload |
| Model activation says the artifact is missing | the seed could not upload to MinIO at first start: `docker compose run --rm seed` again |
| MailHog is empty | `MAIL_MODE` is `log`: the message is in `docker compose logs api` instead |
