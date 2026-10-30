// Tabs shared by the administration pages (tenants, users and roles, alert types).
import { Link, useLocation } from 'react-router-dom';
import Tabs from '@mui/material/Tabs';
import Tab from '@mui/material/Tab';
import { useAuth } from '../../auth/AuthProvider';

const TABS = [
  { path: '/admin/tenants', label: 'Tenants', permission: 'tenants' },
  { path: '/admin/users', label: 'Users and roles', permission: 'users' },
  { path: '/admin/alert-types', label: 'Alert types', permission: 'alert-types' },
];

export function AdminTabs() {
  const location = useLocation();
  const { permissions } = useAuth();
  const visible = TABS.filter((tab) => permissions.includes(tab.permission));
  const current = visible.find((tab) => location.pathname.startsWith(tab.path))?.path ?? false;
  if (visible.length <= 1) return null;
  return (
    <Tabs value={current} sx={{ mb: 1, minHeight: 34, borderBottom: '1px solid #ddd9d0', '& .MuiTab-root': { minHeight: 34, py: 0.5, fontSize: 13, textTransform: 'none' } }}>
      {visible.map((tab) => (
        <Tab key={tab.path} value={tab.path} label={tab.label} component={Link} to={tab.path} data-testid={`tab-${tab.permission}`} />
      ))}
    </Tabs>
  );
}
