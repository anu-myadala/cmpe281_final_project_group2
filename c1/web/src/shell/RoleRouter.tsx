// RoleRouter: the route table built from the permissions list of GET /auth/me.
// A forbidden route redirects to the first allowed page; no session redirects to /login.
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import Box from '@mui/material/Box';
import CircularProgress from '@mui/material/CircularProgress';
import { useAuth } from '../auth/AuthProvider';
import { LiveProvider } from '../live/useLiveEvents';
import { AppShell } from './AppShell';
import { firstAllowedPath } from './menu';
import { LoginPage } from '../pages/LoginPage';
import { OverviewPage } from '../pages/OverviewPage';
import { VehicleMapPage } from '../pages/VehicleMapPage';
import { VehiclesPage } from '../pages/VehiclesPage';
import { SubscriptionsPage } from '../pages/SubscriptionsPage';
import { ReportsPage } from '../pages/ReportsPage';
import { StatsPage } from '../pages/StatsPage';
import { TenantsPage } from '../pages/admin/TenantsPage';
import { UsersPage } from '../pages/admin/UsersPage';
import { AlertTypesPage } from '../pages/admin/AlertTypesPage';
import { ModelsPage } from '../pages/ModelsPage';
import { AlertMonitorPage, AlertDetailPage } from '../pages/AlertMonitorPage';
import { HostedPage } from '../pages/HostedPage';

interface RouteDef {
  path: string;
  permission: string;
  element: JSX.Element;
}

export const ROUTES: RouteDef[] = [
  { path: '/overview', permission: 'overview', element: <OverviewPage /> },
  { path: '/map', permission: 'map', element: <VehicleMapPage /> },
  { path: '/vehicles', permission: 'vehicles', element: <VehiclesPage /> },
  { path: '/vehicles/:vehicleId', permission: 'vehicles', element: <VehiclesPage /> },
  { path: '/subscriptions', permission: 'subscriptions', element: <SubscriptionsPage /> },
  { path: '/reports', permission: 'reports', element: <ReportsPage /> },
  { path: '/stats', permission: 'stats', element: <StatsPage /> },
  { path: '/models', permission: 'models', element: <ModelsPage /> },
  { path: '/alerts', permission: 'alerts', element: <AlertMonitorPage /> },
  { path: '/alerts/:alertId', permission: 'alerts', element: <AlertDetailPage /> },
  { path: '/alert-rules', permission: 'alert-rules', element: <HostedPage page="alert-rules" /> },
  { path: '/devices', permission: 'devices', element: <HostedPage page="devices" /> },
  { path: '/provisioning', permission: 'provisioning', element: <HostedPage page="provisioning" /> },
  { path: '/admin/tenants', permission: 'tenants', element: <TenantsPage /> },
  { path: '/admin/users', permission: 'users', element: <UsersPage /> },
  { path: '/admin/alert-types', permission: 'alert-types', element: <AlertTypesPage /> },
  { path: '/admin/audit', permission: 'audit', element: <HostedPage page="audit" /> },
  { path: '/admin/health', permission: 'health', element: <HostedPage page="health" /> },
];

function Splash() {
  return (
    <Box sx={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <CircularProgress size={28} />
    </Box>
  );
}

export function RoleRouter() {
  const { user, permissions, ready } = useAuth();
  const location = useLocation();
  if (!ready) return <Splash />;
  if (!user) {
    const from = location.pathname === '/login' || location.pathname === '/' ? undefined : location.pathname + location.search;
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace state={from ? { from } : undefined} />} />
      </Routes>
    );
  }
  const home = firstAllowedPath(permissions, user.role);
  const allowed = new Set(permissions);
  return (
    <LiveProvider>
      <Routes>
        <Route path="/login" element={<Navigate to={home} replace />} />
        <Route element={<AppShell />}>
          {ROUTES.map((route) => (
            <Route key={route.path} path={route.path} element={allowed.has(route.permission) ? route.element : <Navigate to={home} replace />} />
          ))}
          <Route path="*" element={<Navigate to={home} replace />} />
        </Route>
      </Routes>
    </LiveProvider>
  );
}
