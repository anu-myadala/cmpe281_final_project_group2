# SCICP C1 demo script (Deliverable #3)

Component C1, Cloud Platform Dashboard and System Admin, with the AI model registry (Anukrithi
Myadala). The same steps serve the **live demo** (UI and back end) and the **recorded scenario
demo**. They run on the EC2 deployment with the real C2, C3 and C4 worker; the laptop stack
behaves the same.

## A. Preparation

1. Fresh data: `cd ~/scicp && bash deploy/reset-demo.sh` (about 2 minutes; it keeps the CLAP
   weights). Wait 5 more minutes so that every car has sent a few clips and each tenant has live
   alerts.
2. Browser tabs: the web app `http://<public-host>/`, MailHog `http://<public-host>:8025/`, and a
   terminal on the instance (EC2 Instance Connect or SSH) in `~/scicp`.
3. The model file for the upload step: `seed/models/1.3.0/model.pt`; its per-class scores to
   paste: `seed/models/1.3.0/per-class.json` (macro-F1 0.99, accuracy 0.99).
4. Accounts, all with the password `Passw0rd!demo`: `anh@example.com` (OWNER),
   `maria@bayshuttle.example` (FLEET_MANAGER), `rider@example.com` (RIDER, used for the lockout),
   `staff@scicp.example` (SERVICE_STAFF), `admin@scicp.example` (SYS_ADMIN).

Back-end views, all read-only: `bash deploy/show.sh services | tests | audit | models | alerts | logs`.

## B. Functions of the component and where each one is shown

| Module (design, Table 4) | Step | Screen | Back end |
|---|---|---|---|
| M1 Authentication and session | 1, 7 | login form, lockout countdown, forced password change | `LOGIN_FAIL`, `LOGIN_LOCK`, `PASSWORD_CHANGE` in the audit log; BR-02 tests |
| M2 Role-based shell | 2, 5, 6 | a different menu per role; the `live` chip | `GET /auth/me` permissions; WebSocket rooms per tenant |
| M3 Dashboard and vehicle map | 2, 5 | overview KPIs, map card, vehicle map with trail and pins | `GET /dashboard/overview`, `GET /dashboard/map` |
| M4 Subscriptions | 3 | services, channels, quiet hours | `PUT /subscriptions/{vehicleId}`, BR-04, `SUBSCRIPTION_SAVE` |
| M5 Statistics and reports | 4, 5 | four report types, statistics, CSV export | `GET /reports/{type}`, `GET /dashboard/stats`, BR-08 |
| M6 System administration | 6 | tenants, users and invitations, alert types | `/admin/*`, BR-06, BR-07, the invitation email |
| M7 Model registry | 8 | versions, metrics, upload, download, activate, roll back, labels | `/models/*`, BR-10, MQTT `model.activated`, the worker's status |
| M8 Audit log (writer) | 9 | (viewer page is design only) | `bash deploy/show.sh audit` |

## C. Steps

1. **Sign-in (M1).** `anh@example.com` with a wrong password: "Invalid email or password."
   (the same answer for an unknown email). `rider@example.com` with a wrong password five times:
   "Too many failed attempts. Account locked until ..." with a countdown (BR-02). Then sign in as
   `anh@example.com`.
2. **Owner dashboard (M2, M3).** Menu of an OWNER; five KPI tiles; the map card with AV-01 moving
   every 10 s and the open alerts as pins; Recent alerts with the live ones on top. **Open** on
   the newest NEW alert: class, confidence, top classes, `model sound-classifier-1.1.0`;
   **Acknowledge**.
3. **Subscriptions (M4).** On SECURITY set "Quiet from" 22:00 only, **Save**: "Quiet
   hours need both times and they must differ" (BR-04). Set "Quiet to" 06:00, save: "Settings
   saved".
4. **Reports (M5).** **Run report** (alerts-by-day): chart and rows. Report type
   `events-by-class`, run. **Export CSV**. Set From to more than 92 days back, run: "Range too
   long: at most 92 days" (BR-08).
5. **Fleet manager (M2, M3, M5).** Sign in as `maria@bayshuttle.example`. **Vehicle map**: five
   cars of her tenant only, AV-13 OFFLINE, AV-14 REGISTERED; click the AV-11 row for its trail,
   tick **Follow the selected car**. **Overview**: within about a minute a toast such as "HIGH
   EMERGENCY alert on AV-12", the badge +1 and a new pin; its email is in MailHog.
   **Statistics**: KPIs, charts, **Export CSV**.
6. **Administration (M6).** Sign in as `admin@scicp.example`. **Tenants** -> **New tenant**
   "Demo Rides LLC". **Users and roles** -> **Invite user** (FLEET_MANAGER of Demo Rides LLC):
   "Invitation sent"; MailHog shows "Your SCICP account" with a temporary password. **Alert
   types** -> SAFETY -> Escalation 20 -> **Save**.
7. **First login of the invited user (M1).** Sign in with the temporary password: the "Set a new
   password" dialog comes first (at least 10 characters). After it: "Overview - Demo Rides LLC"
   with no vehicles.
8. **Model management and deployment (M7).** As `admin@scicp.example`, **AI models**.
   - Versions 1.1.0 ACTIVE (macro-F1 0.68) and 1.2.0 REGISTERED (0.99) with per-class test
     results; Deployment card: `c4-worker-1` runs 1.1.0, READY, SHA-256 verified.
   - **Upload new version**: 1.3.0, `seed/models/1.3.0/model.pt`, macro-F1 0.99, accuracy 0.99,
     the per-class JSON -> "Version 1.3.0 registered", status REGISTERED, the SHA-256 in the panel.
   - **Set as active**: "Target 1.3.0 (macro-F1 0.99), BR-10 passes" -> **Activate**. The
     Deployment card goes LOADING, then READY on 1.3.0.
   - Tenant selector Bay Shuttle LLC -> **Alert monitor**: **Open** on the next new alert:
     `model sound-classifier-1.3.0`.
   - **AI models**, select 1.1.0, **Set as active**: "BR-10 fails: more than 0.05 below the
     active version" and Activate is disabled; **Cancel**. **Export labels** downloads the
     feedback labels of 1.1.0. **Roll back**: 1.1.0 ACTIVE again and the worker follows.
9. **Back end and unit tests (M8).** In the terminal: `bash deploy/show.sh services`,
   `bash deploy/show.sh tests -q` (91 passed), `bash deploy/show.sh audit` (every write above with
   its actor), `bash deploy/show.sh models`, `bash deploy/show.sh alerts` (the model version of
   each alert).

## D. Troubleshooting

| Symptom | Fix |
|---|---|
| The page does not load on EC2 | use `http://`, not `https://`; port 80 open in the security group; `bash deploy/show.sh services` |
| The address stopped working after a stop/start of the instance | `cd ~/scicp && bash deploy/ec2-bootstrap.sh` (it rewrites the public host) |
| "502 Bad Gateway" on sign-in after a restart of the API | `sudo docker compose restart web` |
| No new alerts | `sudo docker compose logs --tail 5 c2-sim` shows a clip per car every 30 to 90 s; `ls c2/edge/datasets/siren` must list WAV files |
| Deployment card empty or FAILED | `bash deploy/show.sh models` shows the worker's last lines; the first start downloads the CLAP weights (600 MB) |
| "Too many failed attempts" for an account you need | it unlocks after 15 minutes, or `bash deploy/reset-demo.sh` |
| Anything left over from a rehearsal | `bash deploy/reset-demo.sh` |
