# SCICP C1 web

Front end of component C1 (Cloud Platform Dashboard and System Admin) with the C4 model registry
pages. React 18, TypeScript, Vite 5, Material UI 5, Recharts, TanStack Query 5, react-router 6,
react-leaflet 4 / Leaflet 1.9 (OpenStreetMap tiles).

## Run

```bash
cd c1/web
npm install
npm run dev            # http://localhost:5173, proxies /api, /ws, /c2, /c3 to http://localhost:8000
```

Start the API first (see `c1/api/README.md`), for example in one process with the inline stubs:

```bash
cd c1/api
DATABASE_URL=sqlite+aiosqlite:///./dev.db MONGO_URL=mock SEED_ON_START=1 STUBS_INLINE=1 \
  C2_BASE_URL=http://localhost:8000/c2/api/v1 C3_BASE_URL=http://localhost:8000/c3/api/v1 \
  uvicorn app.main:app --port 8000
```

Demo logins (password `Passw0rd!demo`): `anh@example.com` (OWNER, tenant A), `maria@bayshuttle.example`
(FLEET_MANAGER, tenant B), `rider@example.com`, `iot@scicp.example`, `staff@scicp.example`, `admin@scicp.example`.

Environment (`.env`, see `.env.example`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `VITE_C2_BASE` | `/c2/api/v1` | base path of the C2 API as the browser reaches it (vehicles, devices, telemetry trail) |
| `VITE_C3_BASE` | `/c3/api/v1` | base path of the C3 API (alert list, alert detail, alert actions, alert rules) |
| `VITE_PROXY_API` | `http://localhost:8000` | where the dev server proxies `/api` and `/ws` |
| `VITE_PROXY_STUBS` | same as the API | where the dev server proxies `/c2` and `/c3` (separate stub process) |

When C2 or C3 is not reachable the widgets that depend on them show a dashed "not available" note
and the page keeps working with the C1 data (overview cards, map positions, subscriptions, reports).

## Scripts

```bash
npm run typecheck      # tsc --noEmit
npm run build          # typecheck + vite build -> dist/
npm run preview        # serve dist/ on 4173 with the same proxies
npm run test:e2e       # Playwright smoke test (starts the API with SQLite + mock MongoDB and the dev server)
```

The smoke test (`tests/smoke.spec.ts`) signs in as the owner, checks the KPI tiles and the map
markers, opens the vehicle map and the subscriptions page (saves a toggle), runs a report, signs out,
signs in as the administrator and checks the tenants, users, alert types and AI model pages,
including the BR-10 confirm dialog.

## Docker

`Dockerfile` builds the bundle with node:20 and serves it with nginx on port 8080.
`nginx/default.conf` proxies `/api` and `/ws` to `api:8000` and `/c2`, `/c3` to `stubs:8002` /
`stubs:8003` (docker-compose service names), so the browser only talks to one origin.

## Layout

```
src/main.tsx, App.tsx          entry, providers (theme, QueryCache, AuthProvider, ToastProvider)
src/theme.ts                   MUI theme and the palette of the GUI style guide
src/api/ApiClient.ts           fetch wrapper (bearer, refresh once on 401, ApiError), C2/C3 bases, download()
src/api/queries.ts             QueryCache (TanStack) keys and hooks, invalidation by live message
src/api/types.ts               wire types
src/auth/AuthProvider.tsx      useAuth: token in memory, refresh timer, mustChangePassword, selected tenant (staff)
src/live/WsClient.ts           WebSocket client: auth, pong, 3 missed pings -> polling, back-off reconnect
src/live/useLiveEvents.tsx     dispatch to the QueryCache, highlighted rows, badge, toasts
src/shell/AppShell.tsx         200 px menu, 52 px top bar, LiveBanner, outlet
src/shell/RoleRouter.tsx       routes from the permissions of GET /auth/me
src/shell/menu.ts              menu entries per permission
src/components/                KpiTile, VehicleCard, VehicleMap, RecentAlertsTable, SubscriptionCard,
                               ServiceToggleRow, QuietHoursPicker, ChartCard, DataTable, FilterBar,
                               ConfirmDialog, Toast, LiveBanner, VersionTable, MetricsTable, StatusChip
src/pages/                     LoginPage, ChangePasswordDialog, OverviewPage, VehicleMapPage, VehiclesPage,
                               SubscriptionsPage, ReportsPage, StatsPage, ModelsPage, UploadVersionDialog,
                               AlertMonitorPage (C3 hosted), HostedPage (C2/C3 placeholders), admin/*
tests/smoke.spec.ts            Playwright smoke test
```
