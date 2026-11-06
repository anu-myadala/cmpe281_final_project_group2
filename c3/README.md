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

## Known seed artifacts

`seed/seed.py` (owned by C1) builds the alert history without applying the alert rules, so 17 of
the 41 seeded alerts would not be created by the AlertEngine. They stay in the data as history;
`tests/test_engine_seed_consistency.py` pins them and fails on any other disagreement.

| Seeded alerts | Engine decision | Reason |
|---|---|---|
| 20443, 20446, 20452, 20455, 20457, 20458, 20460, 20461, 20465, 20476, 20478 (honking, SAFETY) | REPEAT_PENDING | one honking event each; BR-C3-05 needs 2 within 10 s |
| 20444, 20451 (honking on AV-12, SAFETY) | SERVICE_OFF | the seeded subscription has SAFETY off on AV-12 (scenario SC2) |
| 20441, 20454, 20480 (glass_break), 20453 (crash), ACCIDENT | BELOW_THRESHOLD | confidence 0.632 - 0.671, under the 0.70 minimum of rules 2 and 3 |
