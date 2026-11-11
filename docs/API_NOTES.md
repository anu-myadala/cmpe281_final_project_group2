# C1 API implementation notes (what the running backend actually returns)

The backend under `c1/api` follows `docs/BRIEF.md` and Part I of the design. These are the details the
front end, the stubs, and the seed rely on. OpenAPI is served at `/api/v1/docs` when the API runs.

## Seed ids
- Tenants: 1 "Nguyen household" (code tA, BASIC, max 3), 2 "Bay Shuttle LLC" (code tB, FLEET, max 50).
- Users (password `Passw0rd!demo`): 1001 anh@example.com OWNER tA "Anh Nguyen"; 1002 maria@bayshuttle.example FLEET_MANAGER tB "Maria Lopez";
  1003 rider@example.com RIDER tA (active vehicle_rider row on AV-01); 1004 iot@scicp.example IOT_ENGINEER "D. Patel";
  1005 staff@scicp.example SERVICE_STAFF; 1006 admin@scicp.example SYS_ADMIN "Platform Admin".
- Vehicles: vehicle_id 1 = AV-01 (tA, Honda Civic, ACTIVE); 10..14 = AV-10..AV-14 (tB, Tesla Model 3 "Shuttle 1..5"); AV-13 ACTIVE but its
  telemetry stopped 14 min ago (OFFLINE); AV-14 REGISTERED with no telemetry.
- Alerts: 20482 ACCIDENT CRITICAL NEW on AV-01 (event_ref 6729b1c4e1f0a2d3c4b5a697) plus 20441..20480 over 14 days; alert_action rows.
- Mongo: `audio_events` (401 docs, `_id` = event_ref, fields per Part III Table 96, inference.modelVersion "sound-classifier-1.1.0");
  `vehicle_telemetry` (61 samples per online car over the last 10 min around SJSU; meta {tenantId: "tA"/"tB", vehicleId: "AV-01"}).
- Models: ai_model 1 "sound-classifier"; versions 1.1.0 ACTIVE (macroF1 0.84, acc 0.88), 1.2.0 REGISTERED (0.86 / 0.89); 15 feedback labels.
- Sound class ids 1..7 = siren, crash, glass_break, scream, honking, traffic, environmental.

## Auth
- `POST /api/v1/auth/login {email, password}` -> 200 `{accessToken, expiresIn, user{userId, email, name, role, tenantId, plan, mustChangePassword}}`
  and an httpOnly cookie `refreshToken` (Path=/api/v1/auth, SameSite=Strict; Secure only with COOKIE_SECURE=true). 401 UNAUTHORIZED, 423 LOCKED, 429 RATE_LIMIT.
- `POST /auth/refresh` (cookie) -> new accessToken; `POST /auth/logout`; `GET /auth/me` -> user + `permissions[]` page keys:
  OWNER [overview, map, vehicles, alerts, subscriptions, reports]; FLEET_MANAGER + [stats, alert-rules]; RIDER [alerts];
  IOT_ENGINEER [vehicles, devices, provisioning, models, tenants]; SERVICE_STAFF [overview, map, vehicles, alerts, subscriptions, reports, stats, alert-rules, models, tenants];
  SYS_ADMIN all + [users, alert-types, audit, health]. `mustChangePassword` is a client-side gate (show the change-password dialog first).
- `POST /auth/password/change {currentPassword, newPassword}`.
- Error envelope everywhere: `{"error": {"code", "message", "details"}}`.

## Dashboard
- `GET /dashboard/overview?tenantId` -> `{tenant, kpis{vehicles, online, openAlerts, alerts24h, events24h}, vehicles[{vehicleId, vin, label, make, model, year, status, connection, lastSeenAt, deviceSummary, topClass24h, lastEventAt, openAlerts}], recentAlerts[last 10], asOf, degraded}`.
  502 BAD_GATEWAY when C2/C3 are down and nothing is cached (or 200 degraded with OVERVIEW_LOCAL_FALLBACK=1).
- `GET /dashboard/map?tenantId` -> `{vehicles[{vehicleId, label, status, connection (ONLINE|OFFLINE|null when no sample), lat, lon, headingDeg, speedKph, driveMode, ignition, sampleAt}], alerts[{alertId, vehicleId, lat, lon, severity, type, createdAt}], asOf, degraded}`.
  Positions come from MySQL vehicle rows + Mongo vehicle_telemetry (no C2 call); pins from C3 `GET /alerts/map`.
- `GET /dashboard/stats?from&to&tenantId&granularity=auto|hour|day` -> `{granularity, from, to, alertsByTypePerPeriod[], eventsByClass[], deviceStatus{..., degraded?}, falseAlarmRate, meanAckSeconds, totals{alerts, open, events}}`; 422 BR-08 over 92 days.

## Subscriptions
- `GET /subscriptions?vehicleId` -> `[{vehicleId, label, services[{serviceCode, name, available, enabled, channels[], quietFrom, quietTo}], notifyRiders, updatedAt}]` (all six services listed; missing rows appear enabled:false, channels ["DASHBOARD"]).
- `PUT /subscriptions/{vehicleId} {services[{serviceCode, enabled, channels[], quietFrom?, quietTo?}], notifyRiders}` upserts the listed services; 422 BR-04 details `{rule, field, allowed?}`.
- `GET /intelligence-services`.

## Reports
- `GET /reports/{alerts-by-day|events-by-class|vehicle-activity|false-alarms}?from&to&vehicleId&format=json|csv` -> json `{columns, rows, total}` or a CSV stream; defaults to the last 7 days.

## Admin
- `GET/POST/PATCH /admin/tenants` (GET also for IOT_ENGINEER and SERVICE_STAFF; DTO has id, code, name, plan, status, maxVehicles, timeZone, vehicles, updatedAt).
- `GET/POST/PATCH /admin/users`, `POST /admin/users/{id}/reset-password`, `GET/PUT /admin/alert-types`. Pagination `page` (1-based), `size` (default 25, max 100 -> 400).

## Models (C4 registry)
- `GET/POST /models`, `GET /models/{id}/versions`, `POST /models/{id}/versions {version, sizeBytes, sha256?, notes?}` -> `{versionId, version, status, upload{url, key, expiresInSeconds}}` (presigned PUT: a relative signed `/api/v1/storage/objects/...` URL with STORAGE_BACKEND=local, an S3 URL with s3),
  `POST /models/{id}/versions/{vid}/complete {sha256, metrics{macroF1, accuracy, perClass}}`, `POST .../activate {force?, note?}` -> `{active, previous, modelId}` (422 BR-10 details `{rule, activeMacroF1, targetMacroF1}`),
  `POST /models/{id}/rollback`, `GET .../download` -> 302 to the presigned GET (relative Location locally), `GET .../feedback/export` CSV.
- `GET /models/{id}/deployment` (IOT_ENGINEER, SERVICE_STAFF, SYS_ADMIN) -> `{modelId, activeVersion, workers[{workerId, modelVersion, state READY|LOADING|FAILED, loadedAt, sha256, sha256Verified, lastHeartbeatAt, detail, stale, runsActiveVersion}], asOf}`.
- Local storage (STORAGE_BACKEND=local): `PUT /storage/objects/{key}?op=put&exp&sig` (body = bytes, streamed to a temp file and renamed; answers `{key, sizeBytes, sha256}`) and `GET /storage/objects/{key}?op=get&exp&sig` (attachment). `sig` = HMAC-SHA256(JWT_SECRET, "op\nkey\nexp"); a bad or expired signature answers 403 FORBIDDEN. 404 with STORAGE_BACKEND=s3.
- `POST /events/{eventRef}/feedback {label: FALSE_ALARM|CONFIRMED|WRONG_CLASS, correctedClass?}` -> 201.

## WebSocket
- `/ws` (also `/api/v1/ws`): first message `{"type":"auth","token":"<jwt>"}` within 10 s; close 4401 on failure; no ack; server `{"type":"ping"}` every 30 s -> answer `{"type":"pong"}`;
  messages `alert.created {type, alert{...}}`, `alert.updated {type, alertId, status, by}`, `device.status {type, vehicleId, deviceId, status, lastSeenAt, faultReason?, muted?, config?}`, `model.activated {type, modelId, version}`,
  `model.deployed {type, workerId, modelId, modelName, modelVersion, state, loadedAt, sha256, sha256Verified, detail, lastHeartbeatAt}` (staff room only, like model.activated).
  The staff room's copy of a tenant event also carries `tenantId` (numeric), because staff see every tenant: the pages show the live rows and pins of the selected tenant only.

## Internal (service token: JWT with role SERVICE signed with SERVICE_TOKEN_SECRET, mint with `container.tokens.issue_service_token()`)
- `POST /internal/events {topic, payload, tenantId}` (tenantId numeric or code "tA"); alert.created puts the alert under `payload.alert`.
- `POST /internal/subscriptions/defaults {vehicleId, tenantId}` -> 201; `GET /internal/subscriptions?tenantId&vehicleId` -> rows with channels[], quiet hours, notifyRiders, timeZone.
- C4 classifier worker: `GET /internal/models/active?modelId|name` (default `sound-classifier`) -> `{modelId, name, version, tag, sha256, sizeBytes, downloadUrl, expiresInSeconds, activatedAt}` (404 without an ACTIVE version; a relative downloadUrl is resolved against C1_BASE_URL's host);
  `POST /internal/worker/status {workerId, modelId?, modelVersion, sha256?, state: READY|LOADING|FAILED, loadedAt?, detail?}` -> the stored entry; `sha256Verified` is true only for READY with the digest recorded in model_version.
  The simulated worker (stubs/worker.py) is woken by MQTT `scicp/v1/platform/model/activated` (inline: the EventBus) and polls every 30 s; the demo generator stamps `inference.modelVersion` (and `alert.model_version`) of new events with the version the worker has loaded.
- What C1 calls on the stubs: C2 `GET /vehicles?tenantId&size=100` (reads `items`), `GET /vehicles/{vehicleId}/devices`; C3 `GET /alerts?status=OPEN&tenantId&limit&size&sort=createdAt` (items/total),
  `GET /alerts/{alertId}`, `GET /alerts/map?status=OPEN&tenantId` (bare list or {items}). The stubs must accept the same service token.
- Inline stubs: `app.main.stubs_mount(app, container)` imports `stubs.app` (needs `c2_router`, `c3_router` APIRouters and `async def demo_generator(eventbus, mongo_db, mysql_url)`), mounts them at `/c2/api/v1` and `/c3/api/v1` when STUBS_INLINE=1.

## What the web app (c1/web) calls on C2 and C3 directly (same origin, with the USER's bearer JWT, tenantId query parameter)
- C2 base `/c2/api/v1`: `GET /vehicles?tenantId&size=100` -> `{items[...]}`; `GET /vehicles/{vehicleId}/devices?tenantId` -> `[{deviceId, type, status, lastSeenAt}]`;
  `GET /vehicles/{vehicleId}/telemetry?limit=60&tenantId` -> newest-first `[{ts, speedKph, driveMode, ignition, location{type, coordinates:[lon, lat]}, network}]`.
- C3 base `/c3/api/v1`: `GET /alerts?tenantId&status=OPEN|NEW|ACKNOWLEDGED|RESOLVED|FALSE_ALARM&size=100&sort=createdAt` -> `{items, total}` (status omitted = all);
  `GET /alerts/{alertId}?tenantId` -> `{alert{...}, event?, clip?, actions?, notifications?}`; `POST /alerts/{alertId}/actions?tenantId {action: ACKNOWLEDGE|RESOLVE|FALSE_ALARM, note?}` -> the updated alert;
  `GET /alert-rules?tenantId` -> list (placeholder page); `GET /alerts/map?status=OPEN&tenantId` (called by the API).
- The stubs must accept BOTH the user JWT (same JWT_SECRET, enforce the tenant of the token for tenant roles) and the service token (SERVICE_TOKEN_SECRET).
- `alert.created` payload should carry `alert{alertId, vehicleId, type, severity, status, topClass, confidence, createdAt, lat, lon}` so the map pin appears at once.
- Routing: Vite dev proxies /c2 and /c3 to the API process (inline stubs); nginx (c1/web/nginx/default.conf) proxies `/c2/` -> `http://stubs:8002/` and `/c3/` -> `http://stubs:8003/`, so the standalone stub apps serve under `/api/v1/...`.
- Model upload and download go browser -> storage through presigned URLs: locally they are same-origin API paths (nginx: `client_max_body_size 512m`, `proxy_request_buffering off` on /api/); on EC2 the S3 bucket must allow CORS PUT and GET from the web origin.
