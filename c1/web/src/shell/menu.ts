// Menu and route table keyed by the page permissions of GET /auth/me (Table 26).
// `hosted` marks a page of C2 or C3 that the shell hosts (drawn in italic with a star).
import type { Role } from '../api/types';

export interface MenuEntry {
  key: string;
  label: string;
  path: string;
  permission: string;
  hosted?: boolean;
  section?: 'main' | 'admin' | 'iot' | 'intelligence';
}

export const MENU: MenuEntry[] = [
  { key: 'overview', label: 'Overview', path: '/overview', permission: 'overview' },
  { key: 'map', label: 'Vehicle map', path: '/map', permission: 'map' },
  { key: 'vehicles', label: 'My vehicles', path: '/vehicles', permission: 'vehicles' },
  { key: 'subscriptions', label: 'Subscriptions', path: '/subscriptions', permission: 'subscriptions' },
  { key: 'reports', label: 'Reports', path: '/reports', permission: 'reports' },
  { key: 'stats', label: 'Statistics', path: '/stats', permission: 'stats' },
  { key: 'models', label: 'AI models', path: '/models', permission: 'models' },
  { key: 'alerts', label: 'Alert monitor', path: '/alerts', permission: 'alerts', hosted: true },
  { key: 'alert-rules', label: 'Alert rules', path: '/alert-rules', permission: 'alert-rules', hosted: true },
  { key: 'devices', label: 'Devices', path: '/devices', permission: 'devices', hosted: true, section: 'iot' },
  { key: 'provisioning', label: 'Provisioning', path: '/provisioning', permission: 'provisioning', hosted: true, section: 'iot' },
  { key: 'tenants', label: 'Tenants', path: '/admin/tenants', permission: 'tenants', section: 'admin' },
  { key: 'users', label: 'Users and roles', path: '/admin/users', permission: 'users', section: 'admin' },
  { key: 'alert-types', label: 'Alert types', path: '/admin/alert-types', permission: 'alert-types', section: 'admin' },
  { key: 'audit', label: 'Audit log', path: '/admin/audit', permission: 'audit', hosted: true, section: 'admin' },
  { key: 'health', label: 'Platform health', path: '/admin/health', permission: 'health', hosted: true, section: 'admin' },
];

/** Menu entries of a user, in menu order, filtered by the permissions list. */
export function menuItems(permissions: string[], role: Role | null): MenuEntry[] {
  const allowed = new Set(permissions);
  const items = MENU.filter((entry) => allowed.has(entry.permission));
  if (role === 'IOT_ENGINEER') {
    // Table 26: the IoT engineer reads the registry and the tenant list only.
    return items.map((entry) => (entry.key === 'vehicles' ? { ...entry, label: 'Vehicles', hosted: true, section: 'iot' } : entry));
  }
  if (role === 'SERVICE_STAFF' || role === 'SYS_ADMIN') {
    return items.map((entry) => (entry.key === 'vehicles' ? { ...entry, label: 'Vehicles', hosted: true } : entry));
  }
  return items;
}

export function firstAllowedPath(permissions: string[], role: Role | null): string {
  const items = menuItems(permissions, role);
  return items[0]?.path ?? '/login';
}

export function pageTitle(pathname: string): string {
  const entry = MENU.find((item) => pathname === item.path || pathname.startsWith(`${item.path}/`));
  if (entry) return entry.label;
  if (pathname.startsWith('/admin')) return 'Administration';
  return 'SCICP';
}
