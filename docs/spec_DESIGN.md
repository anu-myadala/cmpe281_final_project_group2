# SCICP Deliverable #2 — Component Design: C1 Cloud Platform Dashboard and System Admin (with the C4 Model Registry pages)
Author: Anukrithi Myadala (Group 2: Anukrithi Myadala, Brandon Rivera Venegas, Ray Zhao). Instructor Dr. Jerry Gao, CMPE 281 Fall 2026.
This file is the single source of truth for every name, API, class, table, state and figure in the document. Text sections and figures must agree with it exactly.

## 0. Project context (from Deliverable #1, see ../d1.txt)
SCICP = Smart Car Intelligent Cloud Platform: edge agents in cars record 5 s audio clips, a cloud classifier (CLAP embeddings + head, 7 classes: siren, crash, glass_break, scream, honking, traffic, environmental) classifies them, C3 turns results into alerts (EMERGENCY, ACCIDENT, SECURITY, SAFETY, PASSENGER, VEHICLE_HEALTH), owners/fleet managers/riders are notified, staff monitor everything.
Components: C1 Dashboard and System Admin (this document, owner Anukrithi), C2 Edge Agent and IoT Device Management (Brandon), C3 Alert Tracking and Monitoring (Ray), C4 AI Model Management and Inference (shared; Anukrithi owns the registry pages and APIs described here, the inference worker belongs to C4 proper), C5 Data Management (shared), P ingestion/messaging (MQTT).
Stack: React 18 + TypeScript + Vite + Material UI + Recharts front end; FastAPI (Python 3.11) back end with one module per component, WebSocket /ws; MySQL 8 (InnoDB, UTC), MongoDB 7 (audio_events, vehicle_telemetry), S3 (clips, model artifacts), SES email, Mosquitto MQTT, one EC2 t3.medium with Docker Compose + nginx. JWT bearer tokens carry tenant and role. All REST under /api/v1, JSON. Tenant comes from the token, never the URL.
Roles: OWNER, FLEET_MANAGER, RIDER, IOT_ENGINEER, SERVICE_STAFF, SYS_ADMIN. Tenant roles (OWNER, FLEET_MANAGER, RIDER) have a tenant_id; staff roles (IOT_ENGINEER, SERVICE_STAFF, SYS_ADMIN) have tenant_id NULL.
C1 requirements from D1: FR-C1-01 login/JWT/role views (H); FR-C1-02 owner/fleet overview (H); FR-C1-03 subscriptions per vehicle + email channel (M); FR-C1-04 staff statistics + CSV export (M); FR-C1-05 admin: tenants, users, roles, alert types (M); FR-C1-06 platform health page, audit log, PDF export, per-tenant limits (L, design only). C4 registry: FR-C4-02 register version with file + test results, choose active, roll back (H); FR-C4-03 record model version on each result (C4 worker); FR-C4-04 feedback labels (M).
Seed data used in all examples: Tenant A "Nguyen household" (type OWNER, plan BASIC, 1 car AV-01, owner user anh@example.com); Tenant B "Bay Shuttle LLC" (type FLEET, plan FLEET, five cars AV-10..AV-14, fleet manager maria@bayshuttle.example); staff users: iot@scicp.example (IOT_ENGINEER), staff@scicp.example (SERVICE_STAFF), admin@scicp.example (SYS_ADMIN). Storyboard B creates Tenant C "Campus Rides" (FLEET). Model "sound-classifier" versions: 1.1.0 ACTIVE (macro-F1 0.84), 1.2.0 REGISTERED (0.86), 1.3.0 uploaded in the storyboard (0.87). Sample alert: ACCIDENT, class crash 0.91, vehicle AV-01, alert id 20482, event ref 6729b1c4e1f0a2d3c4b5a697.

## 1. Component overview (Section 1 of the document)
Purpose: the single web entry point of the platform. It authenticates users, decides which view a role sees, shows owners and fleet managers what is happening to their cars, gives service staff statistics and reports, lets the administrator manage tenants, users, roles and alert types, and (C4 pages) lets staff register, activate and roll back classifier versions and export feedback labels.
Objectives: O1 one login and one shell for all six roles; O2 overview within 2 s of login, live within 1 s of an alert (WebSocket, polling fallback 10 s); O3 every write audited; O4 tenant isolation enforced in one place (TenantContext) so no page can leak another tenant's data; O5 statistics on up to 92 days of events without a reporting database; O6 safe model activation (checksum, metrics gate, one-click roll back).
Function scope (modules): M1 Authentication and session (login, refresh, logout, password change, lockout); M2 Role-based shell and navigation (RoleRouter, menu, WebSocket subscription); M3 Owner dashboard (overview KPIs, vehicle cards, recent alerts, polling fallback); M4 Subscriptions; M5 Statistics and reports (stats aggregations, report types, CSV export); M6 System administration (tenants, users and roles, alert types, invitations, password reset); M7 Model registry pages (models, versions, upload via presigned S3 URL, activate, roll back, feedback export); M8 Audit log (writer; the viewer page is design only). Out of scope: alert pages (C3), vehicle/device pages (C2), inference (C4 worker), backups (C5).
Usage: who uses which module: OWNER M1-M5(reports); FLEET_MANAGER M1-M5 (plus statistics for own tenant); RIDER M1, read-only alert page (C3); IOT_ENGINEER M1-M2 + registry read; SERVICE_STAFF M1-M3 (any tenant), M5, M7; SYS_ADMIN everything + M6. Usage scenarios: UC1 owner checks the car after an email alert; UC2 fleet manager tunes subscriptions; UC3 staff exports weekly statistics; UC4 admin onboards a new fleet tenant; UC5 staff promotes a new classifier version and rolls back.
Interfaces to other components: calls C2 REST (GET /vehicles, GET /vehicles/{id}/devices summary) and C3 REST (GET /alerts?status=OPEN&tenantId, GET /alerts/{id}) for overview data; subscribes to C3 events (alert.created, alert.updated) and C2 events (device.status) on the internal event bus and fans them out on /ws; C3 reads subscription rows that C1 writes (BR-05); C2 reads tenant.max_vehicles (BR-06); C4 inference worker reads the ACTIVE model_version row and listens for model.activated; C5 provides the MySQL/MongoDB connections and the audio_events aggregation queries.

## 2. API design (Section 2). All under /api/v1; bearer JWT except login; errors use {error:{code, message, details?}} with codes UNAUTHORIZED 401, FORBIDDEN 403, NOT_FOUND 404, CONFLICT 409, LOCKED 423, VALIDATION 400, BUSINESS_RULE 422 (details.rule = BR-xx), RATE_LIMIT 429.
### 2.1 Authentication
- POST /auth/login {email, password} -> 200 {accessToken (JWT, 15 min: sub, tenantId, role, name, jti), refreshToken (opaque, 7 days, httpOnly cookie), mustChangePassword, user{userId, name, role, tenantId, tenantName}}; 401 invalid credentials (generic), 403 TENANT_SUSPENDED, 423 LOCKED {details.lockedUntil}, 429.
- POST /auth/refresh (cookie) -> 200 new accessToken; 401.
- POST /auth/logout -> 204 (refresh token revoked).
- GET /auth/me -> 200 user + permissions[] (page keys allowed by BR-01).
- POST /auth/password/change {currentPassword, newPassword} -> 204; 400 weak password (min 10 chars, not equal to email); 401 wrong current.
### 2.2 Dashboard
- GET /dashboard/overview?tenantId (staff only; tenant roles ignored) -> {tenant{tenantId,name,plan}, kpis{vehicles, online, openAlerts, alerts24h, events24h}, vehicles[{vehicleId, vin, label, status, lastEventAt, openAlerts, topClass24h}], recentAlerts[{alertId, vehicleId, type, severity, status, topClass, confidence, createdAt}] (last 10), asOf}. 403 for RIDER (riders use C3 alert page). BR-03.
- GET /dashboard/stats?from&to&tenantId&granularity=auto|hour|day -> {granularity, alertsByTypePerPeriod[{period, EMERGENCY, ACCIDENT, SECURITY, SAFETY, PASSENGER, VEHICLE_HEALTH}], eventsByClass[{class, count}], deviceStatus{ONLINE, OFFLINE, FAULT, PROVISIONING, DISABLED}, falseAlarmRate, meanAckSeconds}. 422 BR-08. Roles: SERVICE_STAFF, SYS_ADMIN any tenant; FLEET_MANAGER own tenant.
### 2.3 Subscriptions
- GET /subscriptions?vehicleId -> [{vehicleId, services[{serviceCode, enabled, channels[DASHBOARD|EMAIL|SMS|PUSH], quietFrom, quietTo}], notifyRiders}]
- PUT /subscriptions/{vehicleId} body {services[...], notifyRiders} -> 200 saved row; 422 BR-04 {details.field}; 404 vehicle not in tenant. Roles OWNER, FLEET_MANAGER (write); staff read.
- GET /intelligence-services -> [{serviceCode, name, available, description}] (EMERGENCY, ACCIDENT, SECURITY, SAFETY available; PASSENGER, VEHICLE_HEALTH design only, available=false).
### 2.4 Reports
- GET /reports/{type}?from&to&vehicleId&tenantId&format=json|csv ; type in alerts-by-day, events-by-class, vehicle-activity, false-alarms. json -> {columns[], rows[][], total}; csv -> text/csv streamed, Content-Disposition attachment, 50,000-row cap (422 BR-09). 422 BR-08 range.
### 2.5 Administration (SYS_ADMIN only)
- GET /admin/tenants?status&q&page&size -> page of {tenantId, name, tenantType, plan, maxVehicles, status, vehicles, users, createdAt}
- POST /admin/tenants {name, tenantType OWNER|FLEET|SERVICE_PROVIDER, plan BASIC|FLEET|ENTERPRISE} -> 201 tenant (maxVehicles set from plan: 3/50/500); 409 duplicate name.
- PATCH /admin/tenants/{id} {name?, plan?, status? ACTIVE|SUSPENDED} -> 200; 422 BR-06 (DELETE is not offered).
- GET /admin/users?tenantId&role&status&q&page&size
- POST /admin/users {email, name, role, tenantId?} -> 201 {userId, status ACTIVE, mustChangePassword true}; sends SES invitation; 409 email exists; 400 BR-07 tenant/role mismatch.
- PATCH /admin/users/{id} {name?, role?, status? ACTIVE|DISABLED, tenantId?} -> 200; 422 BR-07 last admin.
- POST /admin/users/{id}/reset-password -> 202 (temporary password emailed; must_change_password true).
- GET /admin/alert-types -> [{code, name, defaultSeverity, escalationMinutes, description}]; PUT /admin/alert-types/{code} {name?, defaultSeverity?, escalationMinutes?} -> 200 (codes are fixed; C3 reads the table).
- GET /admin/audit?actor&entity&from&to&page (design only, L).
### 2.6 Model registry (C4 pages; SERVICE_STAFF, SYS_ADMIN write; IOT_ENGINEER read)
- GET /models -> [{modelId, name, task, activeVersion, versions}]; POST /models {name, task} -> 201.
- GET /models/{id}/versions -> [{versionId, version, status, metrics{macroF1, accuracy, perClass{...}}, artifactUri, sha256, sizeBytes, createdBy, createdAt, activatedAt, notes, labelCount}]
- POST /models/{id}/versions {version (semver, unique per model), notes?} -> 201 {versionId, upload{url (S3 presigned PUT, 15 min), key}} (status UPLOADING).
- POST /models/{id}/versions/{v}/complete {sha256, sizeBytes, metrics{...}} -> 200 status REGISTERED; 422 checksum mismatch or missing metrics; 409 if not UPLOADING.
- POST /models/{id}/versions/{v}/activate {force?: bool, note?} -> 200 {active: v, previous: v'}; 404; 409 status not REGISTERED/INACTIVE; 422 BR-10 {details.rule, details.activeMacroF1, details.targetMacroF1}.
- POST /models/{id}/rollback {note} -> 200 (activates the most recently INACTIVE version that was previously ACTIVE); 409 nothing to roll back to.
- GET /models/{id}/versions/{v}/download -> 302 presigned GET (15 min).
- POST /events/{eventRef}/feedback {label CONFIRMED|FALSE_ALARM|WRONG_CLASS, correctedClass?} -> 201; 409 BR-11 duplicate per user. (Called from the C3 alert detail page.)
- GET /models/{id}/feedback/export?versionId&format=csv -> csv (eventRef, versionId, predictedClass, confidence, label, correctedClass, userId, ts); 404 BR-11 no labels.
### 2.7 WebSocket /ws?token=... (JWT in first message {type:'auth', token})
Server -> client messages: {type:'alert.created', alert{...}}, {type:'alert.updated', alertId, status, by}, {type:'device.status', vehicleId, deviceId, status, lastSeenAt}, {type:'model.activated', modelId, version}, {type:'ping'}. Rooms: tenant:{tenantId} for tenant roles; staff room receives every tenant's events plus model.activated. Client sends {type:'pong'} every 30 s; on 3 missed pings the client switches to polling GET /dashboard/overview every 10 s and shows the yellow banner.
### 2.8 Internal interfaces (not public)
- Event bus (in-process pub/sub, Redis in full design): C3 publishes alert.created/updated; C2 publishes device.status; C1 WsHub fans out; ModelRegistryService publishes model.activated to the C4 worker.
- C1 -> C2 VehicleClient.list(tenantId), C1 -> C3 AlertClient.openAlerts(tenantId, limit) over internal REST with a service token.
- C5 DataAccess: MySQL session (SQLAlchemy), MongoDB client (Motor), aggregation EventQueryRepo.

## 3. Function design, data design, behavior (Section 3). MASTER class list — diagrams use exactly these names.
### 3.1 Back-end classes (FastAPI module app/c1 and app/c4registry)
Routers (thin): AuthRouter, DashboardRouter, SubscriptionRouter, ReportRouter, AdminRouter, ModelRouter, WsRouter.
Services:
- AuthService: login(email, password) -> TokenPair; refresh(refreshToken); logout(jti); me(userId); changePassword(userId, current, new). Uses UserRepo, TenantRepo, LoginThrottle, TokenService, AuditLogger, PasswordHasher (argon2).
- LoginThrottle: owns failed_logins, last_failed_at, locked_until on app_user; record_failure(user) -> locked?; reset(user); is_locked(user). 5 failures / 15 min window / 15 min lock.
- TokenService: issue(user) -> access JWT (HS256, 15 min) + refresh (opaque, stored hashed in refresh_token table, 7 days); verify(jwt) -> Claims; revoke(jti).
- RbacGuard (FastAPI dependency): requires(*roles); page_permissions(role) -> list (BR-01 table).
- TenantContext: resolve(claims, requested_tenant_id?) -> tenant_id scope (BR-03): tenant roles always own tenant; staff may pass tenantId; RIDER -> vehicles with active vehicle_rider rows.
- DashboardService: overview(scope) -> OverviewDto (VehicleClient + AlertClient + EventQueryRepo); stats(scope, from, to, granularity) -> StatsDto (BR-08).
- SubscriptionService: list(scope, vehicleId?); save(scope, vehicleId, SubscriptionDto, actor) (BR-04, audit); defaults(vehicleId) when a vehicle is created (called by C2 hook).
- ReportService: run(type, scope, from, to, vehicleId?) -> ReportResult(columns, rows); export_csv(...) streaming (BR-08, BR-09). Report types: AlertsByDayReport, EventsByClassReport, VehicleActivityReport, FalseAlarmReport (strategy classes implementing ReportType.query()).
- AdminService: list_tenants, create_tenant, update_tenant (BR-06); list_users, create_user (invite), update_user (BR-07), reset_password; list_alert_types, update_alert_type. Uses TenantRepo, UserRepo, AlertTypeRepo, MailSender, PasswordHasher, AuditLogger.
- ModelRegistryService: list_models, create_model, list_versions, begin_upload(modelId, version, notes) -> presigned PUT (StorageClient), complete_upload(sha256, size, metrics) (verifies S3 object head + sha256), activate(modelId, version, force, note, actor) (BR-10, transaction, EventBus.publish model.activated), rollback(modelId, note, actor), record_feedback (BR-11), export_feedback. Uses ModelRepo, ModelVersionRepo, FeedbackRepo, StorageClient, AuditLogger, EventBus.
- AuditLogger: write(actor, tenantId, action, entity, entityId, before, after) (BR-12). Actions: LOGIN, LOGIN_FAIL, LOGIN_LOCK, LOGOUT, PASSWORD_CHANGE, SUBSCRIPTION_SAVE, TENANT_CREATE, TENANT_UPDATE, USER_CREATE, USER_UPDATE, USER_RESET, ALERT_TYPE_UPDATE, MODEL_CREATE, VERSION_UPLOAD, VERSION_COMPLETE, MODEL_ACTIVATE, MODEL_ROLLBACK, FEEDBACK_LABEL.
- WsHub: connect(ws, claims); rooms dict; broadcast(room, message); on EventBus events -> fan out; heartbeat.
- EventBus: publish(topic, payload); subscribe(topic, handler).
- MailSender (SES): send_invitation(user, tempPassword); send_reset(user, tempPassword).
- StorageClient (S3): presign_put(key, 15 min); presign_get(key); head(key) -> (size, etag); the key pattern models/{modelId}/{version}/model.pt.
- VehicleClient (C2 REST), AlertClient (C3 REST): internal clients with service token.
Repositories (SQLAlchemy, MySQL): TenantRepo, UserRepo, RefreshTokenRepo, SubscriptionRepo, IntelligenceServiceRepo, AlertTypeRepo, AuditRepo, ModelRepo, ModelVersionRepo, FeedbackRepo. MongoDB: EventQueryRepo (events_by_class(scope, from, to), alerts_by_period via AlertClient or MySQL alert table read-only through C5 view v_alert_summary, false_alarm_rate(scope, from, to)).
DTOs: TokenPair, Claims, OverviewDto, StatsDto, SubscriptionDto, ReportResult, TenantDto, UserDto, VersionDto, UploadTicket.
### 3.2 Entities (MySQL tables owned by C1 and C4 registry)
tenant(tenant_id PK BIGINT, name UQ, tenant_type ENUM(OWNER,FLEET,SERVICE_PROVIDER), plan ENUM(BASIC,FLEET,ENTERPRISE), max_vehicles INT, status ENUM(ACTIVE,SUSPENDED), created_at, updated_at)
app_user(user_id PK, tenant_id FK NULL, email UQ, password_hash, name, role ENUM(6), status ENUM(ACTIVE,DISABLED,LOCKED), failed_logins INT, last_failed_at, locked_until, must_change_password BOOL, last_login_at, created_at) index (tenant_id, role)
refresh_token(token_id PK, user_id FK, token_hash UQ, expires_at, revoked_at)
intelligence_service(service_code PK, name, description, available BOOL)
subscription(subscription_id PK, tenant_id FK, vehicle_id FK, service_code FK, enabled BOOL, channels SET(DASHBOARD,EMAIL,SMS,PUSH), quiet_from TIME NULL, quiet_to TIME NULL, notify_riders BOOL, updated_by FK, updated_at) UQ (vehicle_id, service_code), index (tenant_id, vehicle_id)
alert_type(code PK, name, default_severity ENUM(LOW,MEDIUM,HIGH,CRITICAL), escalation_minutes INT, description)
audit_log(audit_id PK, actor_user_id FK NULL, tenant_id NULL, action VARCHAR, entity VARCHAR, entity_id VARCHAR, before_json JSON, after_json JSON, ip, ts) index (tenant_id, ts), (entity, entity_id)
ai_model(model_id PK, name UQ, task, created_by, created_at)
model_version(version_id PK, model_id FK, version VARCHAR semver, status ENUM(UPLOADING,REGISTERED,ACTIVE,INACTIVE,FAILED), artifact_uri, sha256 CHAR(64), size_bytes, metrics_json JSON, notes, created_by, created_at, activated_at, deactivated_at) UQ (model_id, version); at most one ACTIVE per model (enforced in the activate transaction with SELECT ... FOR UPDATE)
feedback_label(label_id PK, event_ref CHAR(24), version_id FK, user_id FK, label ENUM(CONFIRMED,FALSE_ALARM,WRONG_CLASS), corrected_class, ts) UQ (event_ref, user_id)
model_deployment(deployment_id, version_id, target ENUM(PRIMARY,SHADOW,CANARY), traffic_pct, started_at, ended_at) — full design only.
Read-only from other components: vehicle (C2), vehicle_rider (C2), alert (C3) through C5 views; audio_events (MongoDB) through EventQueryRepo.
### 3.3 Front-end classes (React + TypeScript)
AppShell (menu, top bar, outlet), RoleRouter (route table from permissions), AuthProvider/useAuth (tokens, refresh timer, mustChangePassword gate), ApiClient (fetch wrapper, 401 -> refresh once -> logout), WsClient/useLiveEvents (connect, auth, rooms, pong, fallback polling), QueryCache (TanStack Query). Pages: LoginPage, ChangePasswordDialog, OverviewPage, VehiclesPage, SubscriptionsPage, ReportsPage, StatsPage, TenantsPage, UsersPage, AlertTypesPage, ModelsPage, (AuditLogPage, HealthPage design only). Components: KpiTile, VehicleCard, RecentAlertsTable, SubscriptionCard, ServiceToggleRow, QuietHoursPicker, ChartCard (Recharts), DataTable, FilterBar, ConfirmDialog, Toast, LiveBanner, VersionTable, UploadVersionDialog (reads file, computes SHA-256 in browser with SubtleCrypto, PUTs to presigned URL, then calls complete), MetricsTable.
### 3.4 Behavior
State diagram 1, user session: Anonymous -> (submit) Authenticating -> [ok] Active -> (access token near expiry) Refreshing -> Active; Active -> (logout | refresh fails) Anonymous; Authenticating -> [wrong password] Anonymous; Authenticating -> [5th failure] Locked (15 min timer) -> Anonymous; Active with mustChangePassword -> PasswordChangeRequired -> Active.
State diagram 2, model version: Uploading -> (complete ok) Registered; Uploading -> (checksum/metrics fail) Failed; Registered -> (activate passes BR-10) Active; Active -> (another version activated) Inactive; Inactive -> (activate or rollback) Active; Registered/Inactive -> (archive, design only) Archived.
State diagram 3, tenant and subscription: Tenant: Active <-> Suspended (admin PATCH); Subscription per service: Enabled <-> Disabled (owner save); Enabled -> Quiet (inside quiet hours, derived) -> Enabled.
Sequence diagrams: SQ1 login (LoginPage -> AuthRouter -> AuthService -> UserRepo/LoginThrottle/TokenService/AuditLogger); SQ2 overview load (OverviewPage -> DashboardRouter -> TenantContext -> DashboardService -> VehicleClient(C2), AlertClient(C3), EventQueryRepo); SQ3 overview live update (C3 -> EventBus -> WsHub -> WsClient -> OverviewPage highlight; fallback polling); SQ4 statistics and CSV export (StatsPage -> DashboardService.stats; ReportsPage -> ReportService.export_csv streaming); SQ5 admin creates tenant and invites user (TenantsPage/UsersPage -> AdminService -> TenantRepo/UserRepo/MailSender/AuditLogger); SQ6 invited user first login with forced password change; SQ7 model upload (UploadVersionDialog -> begin_upload -> S3 PUT -> complete_upload -> head + sha check); SQ8 model activate and roll back (ModelsPage -> ModelRegistryService -> transaction -> EventBus model.activated -> C4 worker reload); SQ9 subscription save (SubscriptionsPage -> SubscriptionService -> BR-04 -> SubscriptionRepo -> AuditLogger; C3 reads row at next alert).
Flowcharts: FC1 login decision (BR-02); FC2 activate version (BR-10); FC3 stats and export range (BR-08/09).
Pseudocode functions to show: AuthService.login, LoginThrottle.record_failure, TenantContext.resolve, DashboardService.overview, SubscriptionService.save (validate), ReportService.export_csv (streaming generator), AdminService.create_user, ModelRegistryService.activate (transaction), WsHub.on_event.
Program function tables: one row per public method: name, inputs, outputs, rules, errors.

## 4. Business logics: already written in s4.js (BR-01..BR-12 + decision tables). ## 5. GUI: already written in s5.js.

## 6. Figures (file names referenced by the text; all PNG, white background, ~150 dpi, width 1600-2200 px)
fig_context.png — context diagram: C1 in the middle; users (6 roles) on the left via browser HTTPS; C2, C3, C4 worker, C5 data, S3, SES around; arrows labeled with the interface (REST, WebSocket, event bus, SQL/Mongo).
fig_modules.png — internal structure: three layers (React pages / FastAPI routers+services / repositories+clients) with modules M1-M8 placed.
class_backend.png — services + routers + guards (classes with key methods), associations.
class_backend_repos.png — repositories, clients, StorageClient, MailSender, EventBus, WsHub with methods.
class_entities.png — entity classes = the MySQL tables with attributes, PK/FK, multiplicities (tenant 1-* app_user, tenant 1-* subscription, vehicle 1-* subscription, intelligence_service 1-* subscription, ai_model 1-* model_version, model_version 1-* feedback_label, app_user 1-* audit_log).
class_frontend.png — pages, hooks, components with props/state.
state_session.png, state_model_version.png, state_tenant_subscription.png — as in 3.4.
seq_login.png, seq_overview.png, seq_overview_live.png, seq_stats_export.png, seq_admin.png, seq_admin_first_login.png, seq_model_upload.png, seq_model_activate.png, seq_subscription.png — as in 3.4 (seq_model_activate includes the roll back alt frame).
flow_login.png, flow_activate.png, flow_stats.png — flowcharts.
fig_partition_tree.png — UI function partition tree: root "C1 dashboard and admin UI"; children: Login; Owner view (Overview, My vehicles, Subscriptions, Reports, Alerts*(C3)); Staff view (Statistics, Reports, AI models (Versions, Upload, Activate, Roll back, Feedback export), Alert monitor*(C3), Vehicles*(C2)); Admin pages (Tenants, Users and roles, Alert types, Audit log*, Platform health*). * = hosted by another component or design only.
fig_storyboard.png — two rows of 5 mini screens with arrows: A owner (Login -> Overview -> Alert detail (C3) -> Subscriptions -> Saved toast); B admin/staff (Login -> Tenants: New tenant Campus Rides -> Users: Invite maria2 FLEET_MANAGER -> AI models: upload 1.3.0, confirm activate -> Statistics: export CSV).
fig_ui_markup.png — one page frame with 12 numbered regions: 1 logo, 2 left menu, 3 top bar title, 4 global search, 5 open-alert badge, 6 user menu; 7 KPI/filter row, 8 main content, 9 side/detail panel, 10 primary actions, 11 toast area, 12 dialog layer.
fig_wf_login.png, fig_wf_owner.png, fig_wf_subscriptions.png, fig_wf_reports.png, fig_wf_stats.png, fig_wf_admin.png, fig_wf_models.png — grayscale wireframes with the seed data (Tenant A, AV-01, alert ACCIDENT crash 0.91; versions 1.1.0 ACTIVE 0.84, 1.2.0 REGISTERED 0.86, 1.3.0), buttons labeled as in 5.5.
