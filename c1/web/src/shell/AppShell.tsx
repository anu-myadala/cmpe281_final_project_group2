// AppShell: 200 px left menu filtered by role (icons only below 1024 px), 52 px top bar
// (page title, tenant selector for staff roles, global search, live indicator, open-alert
// badge, user menu), the LiveBanner and the outlet for the page.
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';
import InputBase from '@mui/material/InputBase';
import Select from '@mui/material/Select';
import MenuItem from '@mui/material/MenuItem';
import IconButton from '@mui/material/IconButton';
import Badge from '@mui/material/Badge';
import Menu from '@mui/material/Menu';
import Tooltip from '@mui/material/Tooltip';
import Divider from '@mui/material/Divider';
import useMediaQuery from '@mui/material/useMediaQuery';
import DashboardOutlinedIcon from '@mui/icons-material/DashboardOutlined';
import MapOutlinedIcon from '@mui/icons-material/MapOutlined';
import DirectionsCarOutlinedIcon from '@mui/icons-material/DirectionsCarOutlined';
import NotificationsActiveOutlinedIcon from '@mui/icons-material/NotificationsActiveOutlined';
import AssessmentOutlinedIcon from '@mui/icons-material/AssessmentOutlined';
import BarChartOutlinedIcon from '@mui/icons-material/BarChartOutlined';
import MemoryOutlinedIcon from '@mui/icons-material/MemoryOutlined';
import WarningAmberOutlinedIcon from '@mui/icons-material/WarningAmberOutlined';
import RuleOutlinedIcon from '@mui/icons-material/RuleOutlined';
import DevicesOutlinedIcon from '@mui/icons-material/DevicesOutlined';
import AddLinkOutlinedIcon from '@mui/icons-material/AddLinkOutlined';
import BusinessOutlinedIcon from '@mui/icons-material/BusinessOutlined';
import PeopleOutlineIcon from '@mui/icons-material/PeopleOutline';
import LabelOutlinedIcon from '@mui/icons-material/LabelOutlined';
import HistoryOutlinedIcon from '@mui/icons-material/HistoryOutlined';
import MonitorHeartOutlinedIcon from '@mui/icons-material/MonitorHeartOutlined';
import SearchIcon from '@mui/icons-material/Search';
import AccountCircleOutlinedIcon from '@mui/icons-material/AccountCircleOutlined';
import { useAuth } from '../auth/AuthProvider';
import { useLiveEvents } from '../live/useLiveEvents';
import { useOverview, useTenants } from '../api/queries';
import { LiveBanner } from '../components/common';
import { ChangePasswordDialog } from '../pages/ChangePasswordDialog';
import { colors } from '../theme';
import { menuItems, pageTitle, type MenuEntry } from './menu';

const ICONS: Record<string, ReactNode> = {
  overview: <DashboardOutlinedIcon fontSize="small" />,
  map: <MapOutlinedIcon fontSize="small" />,
  vehicles: <DirectionsCarOutlinedIcon fontSize="small" />,
  subscriptions: <NotificationsActiveOutlinedIcon fontSize="small" />,
  reports: <AssessmentOutlinedIcon fontSize="small" />,
  stats: <BarChartOutlinedIcon fontSize="small" />,
  models: <MemoryOutlinedIcon fontSize="small" />,
  alerts: <WarningAmberOutlinedIcon fontSize="small" />,
  'alert-rules': <RuleOutlinedIcon fontSize="small" />,
  devices: <DevicesOutlinedIcon fontSize="small" />,
  provisioning: <AddLinkOutlinedIcon fontSize="small" />,
  tenants: <BusinessOutlinedIcon fontSize="small" />,
  users: <PeopleOutlineIcon fontSize="small" />,
  'alert-types': <LabelOutlinedIcon fontSize="small" />,
  audit: <HistoryOutlinedIcon fontSize="small" />,
  health: <MonitorHeartOutlinedIcon fontSize="small" />,
};

function MenuLink({ entry, active, collapsed }: { entry: MenuEntry; active: boolean; collapsed: boolean }) {
  const content = (
    <Box
      component={Link}
      to={entry.path}
      data-testid={`menu-${entry.key}`}
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1,
        px: collapsed ? 0 : 1.5,
        justifyContent: collapsed ? 'center' : 'flex-start',
        py: 0.8,
        fontSize: 13.5,
        textDecoration: 'none',
        color: active ? colors.primaryDark : entry.hosted ? '#666' : '#333',
        fontStyle: entry.hosted && !active ? 'italic' : 'normal',
        fontWeight: active ? 600 : 400,
        bgcolor: active ? colors.primaryLight : 'transparent',
        borderLeft: `4px solid ${active ? colors.primary : 'transparent'}`,
        '&:hover': { bgcolor: active ? colors.primaryLight : '#eceae4' },
      }}
    >
      {ICONS[entry.key]}
      {!collapsed && (
        <span>
          {entry.label}
          {entry.hosted ? ' *' : ''}
        </span>
      )}
    </Box>
  );
  return collapsed ? (
    <Tooltip title={entry.label} placement="right">
      {content}
    </Tooltip>
  ) : (
    content
  );
}

function TenantSelector() {
  const { isStaff, tenantId, setSelectedTenant } = useAuth();
  const tenants = useTenants({}, isStaff);
  const items = tenants.data?.items ?? [];
  useEffect(() => {
    if (isStaff && tenantId === null && items.length > 0) setSelectedTenant(items[0].tenantId);
  }, [isStaff, tenantId, items, setSelectedTenant]);
  if (!isStaff) return null;
  return (
    <Select
      value={tenantId !== null && items.some((t) => t.tenantId === tenantId) ? String(tenantId) : ''}
      onChange={(event) => setSelectedTenant(Number(event.target.value))}
      displayEmpty
      size="small"
      data-testid="tenant-selector"
      sx={{ height: 28, fontSize: 12, mr: 1.5, minWidth: 190, '& .MuiSelect-select': { py: 0.4 } }}
      renderValue={(value) => {
        const tenant = items.find((t) => String(t.tenantId) === value);
        return `Tenant: ${tenant ? tenant.name : tenants.isLoading ? '...' : 'select'}`;
      }}
    >
      {items.map((tenant) => (
        <MenuItem key={tenant.tenantId} value={String(tenant.tenantId)}>
          {tenant.name} ({tenant.code}) - {tenant.plan}
        </MenuItem>
      ))}
    </Select>
  );
}

function OpenAlertBadge() {
  const { tenantId, permissions } = useAuth();
  const { badgePulse, polling } = useLiveEvents();
  const canSee = permissions.includes('overview') || permissions.includes('alerts');
  const overview = useOverview(canSee ? tenantId : null, polling);
  const [pulse, setPulse] = useState(false);
  useEffect(() => {
    if (!badgePulse) return;
    setPulse(true);
    const timer = window.setTimeout(() => setPulse(false), 900);
    return () => window.clearTimeout(timer);
  }, [badgePulse]);
  if (!canSee) return null;
  const count = overview.data?.kpis.openAlerts ?? 0;
  return (
    <Tooltip title="Open alerts">
      <IconButton
        component={Link}
        to="/alerts"
        size="small"
        data-testid="open-alert-badge"
        sx={{
          border: '1px solid #c9c7c0',
          borderRadius: 1.5,
          width: 34,
          height: 28,
          mr: 1.5,
          transform: pulse ? 'scale(1.15)' : 'none',
          transition: 'transform .3s ease',
        }}
      >
        <Badge
          badgeContent={count}
          color="error"
          max={99}
          sx={{ '& .MuiBadge-badge': { fontSize: 10, height: 16, minWidth: 16, bgcolor: '#b42318', top: -4, right: -6 } }}
        >
          <WarningAmberOutlinedIcon sx={{ fontSize: 16, color: '#555' }} />
        </Badge>
      </IconButton>
    </Tooltip>
  );
}

export function AppShell() {
  const { user, permissions, logout, mustChangePassword, tenantId } = useAuth();
  const { connected, polling } = useLiveEvents();
  const location = useLocation();
  const navigate = useNavigate();
  const narrow = useMediaQuery('(max-width:1024px)');
  const [searchText, setSearchText] = useState('');
  const [userMenu, setUserMenu] = useState<null | HTMLElement>(null);
  const [changePassword, setChangePassword] = useState(false);
  const tenants = useTenants({}, Boolean(user && ['SERVICE_STAFF', 'SYS_ADMIN', 'IOT_ENGINEER'].includes(user.role)));

  const items = useMemo(() => menuItems(permissions, user?.role ?? null), [permissions, user?.role]);
  const collapsed = narrow;

  const tenantName = user?.tenantName ?? tenants.data?.items.find((t) => t.tenantId === tenantId)?.name;
  const title = `${pageTitle(location.pathname)}${tenantName && !location.pathname.startsWith('/admin') && !location.pathname.startsWith('/models') ? ` - ${tenantName}` : ''}`;

  useEffect(() => {
    document.title = `${title} | SCICP`;
  }, [title]);

  const submitSearch = () => {
    const text = searchText.trim();
    if (!text) return;
    if (/^AV-\d+$/i.test(text)) navigate(`/map?vehicle=${text.toUpperCase()}`);
    else if (/^\d+$/.test(text)) navigate(`/alerts/${text}`);
    else if (permissions.includes('users')) navigate(`/admin/users?q=${encodeURIComponent(text)}`);
    else navigate(`/vehicles?q=${encodeURIComponent(text)}`);
  };

  const sections: Array<{ key: string; label?: string; entries: MenuEntry[] }> = [];
  const main = items.filter((entry) => !entry.section || entry.section === 'main');
  const iot = items.filter((entry) => entry.section === 'iot');
  const admin = items.filter((entry) => entry.section === 'admin');
  if (user?.role === 'IOT_ENGINEER') {
    sections.push({ key: 'iot', label: 'IoT and devices', entries: [...main.filter((e) => e.key === 'vehicles'), ...iot] });
    sections.push({ key: 'intelligence', label: 'Intelligence', entries: main.filter((e) => e.key !== 'vehicles') });
    sections.push({ key: 'admin', label: 'Platform', entries: admin });
  } else {
    sections.push({ key: 'main', entries: main });
    if (iot.length) sections.push({ key: 'iot', label: 'IoT and devices', entries: iot });
    if (admin.length) sections.push({ key: 'admin', label: 'Administration', entries: admin });
  }

  const menuWidth = collapsed ? 56 : 200;

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh', bgcolor: colors.contentBg }}>
      <Box
        component="nav"
        aria-label="Main menu"
        sx={{
          width: menuWidth,
          flexShrink: 0,
          bgcolor: colors.menuBg,
          borderRight: `1px solid ${colors.border}`,
          position: 'fixed',
          top: 0,
          bottom: 0,
          left: 0,
          display: 'flex',
          flexDirection: 'column',
          zIndex: 10,
        }}
      >
        <Box sx={{ height: 52, borderBottom: `1px solid ${colors.border}`, display: 'flex', alignItems: 'center', pl: collapsed ? 0 : 1.75, justifyContent: collapsed ? 'center' : 'flex-start', fontWeight: 700, fontSize: 15 }}>
          <Box sx={{ width: 22, height: 22, borderRadius: 1.5, bgcolor: colors.primary, mr: collapsed ? 0 : 1 }} />
          {!collapsed && 'SCICP'}
        </Box>
        <Box sx={{ pt: 1, flex: 1, overflowY: 'auto' }}>
          {sections.map((section, index) => (
            <Box key={section.key}>
              {index > 0 && !section.label && <Divider sx={{ my: 0.75, mx: 1.75 }} />}
              {section.label && !collapsed && (
                <Typography sx={{ fontSize: 11, letterSpacing: '.06em', color: '#6b7280', textTransform: 'uppercase', px: 1.75, pt: 1.25, pb: 0.25 }}>
                  {section.label}
                </Typography>
              )}
              {section.label && collapsed && <Divider sx={{ my: 0.75, mx: 1 }} />}
              {section.entries.map((entry) => (
                <MenuLink key={entry.key} entry={entry} collapsed={collapsed} active={location.pathname === entry.path || location.pathname.startsWith(`${entry.path}/`)} />
              ))}
            </Box>
          ))}
        </Box>
        {!collapsed && user && (
          <Typography sx={{ fontSize: 12, color: '#555', px: 1.75, py: 1.25 }} data-testid="role-note">
            {user.role} view
          </Typography>
        )}
      </Box>

      <Box sx={{ flex: 1, ml: `${menuWidth}px`, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        <Box
          component="header"
          sx={{
            height: 52,
            bgcolor: '#fff',
            borderBottom: `1px solid ${colors.border}`,
            display: 'flex',
            alignItems: 'center',
            px: 1.75,
            position: 'sticky',
            top: 0,
            zIndex: 9,
          }}
        >
          <Typography variant="h6" component="h1" sx={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} data-testid="page-title">
            {title}
          </Typography>
          <TenantSelector />
          <Box
            component="form"
            onSubmit={(event) => {
              event.preventDefault();
              submitSearch();
            }}
            sx={{ display: { xs: 'none', md: 'flex' }, alignItems: 'center', width: 220, height: 28, border: '1px solid #c9c7c0', borderRadius: 14, px: 1.25, mr: 1.5, bgcolor: '#fff' }}
          >
            <SearchIcon sx={{ fontSize: 15, color: '#777', mr: 0.5 }} />
            <InputBase
              placeholder="Search vehicles, alerts, users..."
              value={searchText}
              onChange={(event) => setSearchText(event.target.value)}
              inputProps={{ 'aria-label': 'Search', style: { fontSize: 12, padding: 0 } }}
              sx={{ flex: 1 }}
            />
          </Box>
          <Box
            data-testid="live-indicator"
            data-live={connected ? 'true' : 'false'}
            sx={{
              height: 24,
              borderRadius: 12,
              px: 1.25,
              mr: 1.5,
              fontSize: 11.5,
              fontWeight: 600,
              display: 'flex',
              alignItems: 'center',
              bgcolor: connected ? colors.primaryLight : '#eee',
              color: connected ? colors.primaryDark : '#666',
            }}
          >
            <Box component="span" sx={{ mr: 0.5, fontSize: 10 }}>
              {'●'}
            </Box>
            {connected ? 'live' : polling ? 'polling' : 'offline'}
          </Box>
          <OpenAlertBadge />
          <Box sx={{ borderLeft: `1px solid ${colors.border}`, pl: 1.25, display: 'flex', alignItems: 'center', gap: 0.75 }}>
            <Box sx={{ textAlign: 'right', fontSize: 12, lineHeight: 1.2, display: { xs: 'none', sm: 'block' } }}>
              <Box sx={{ fontWeight: 600 }} data-testid="user-name">
                {user?.name}
              </Box>
              <Box sx={{ color: '#555' }}>
                {user?.role}
                {user?.tenantName ? ` · ${user.tenantName}` : ''}
              </Box>
            </Box>
            <IconButton size="small" onClick={(event) => setUserMenu(event.currentTarget)} aria-label="User menu" data-testid="user-menu">
              <AccountCircleOutlinedIcon />
            </IconButton>
            <Menu open={Boolean(userMenu)} anchorEl={userMenu} onClose={() => setUserMenu(null)}>
              <MenuItem disabled sx={{ fontSize: 12 }}>
                {user?.email}
              </MenuItem>
              <MenuItem
                onClick={() => {
                  setUserMenu(null);
                  setChangePassword(true);
                }}
              >
                Change password
              </MenuItem>
              <MenuItem
                data-testid="logout"
                onClick={async () => {
                  setUserMenu(null);
                  await logout();
                  navigate('/login');
                }}
              >
                Sign out
              </MenuItem>
            </Menu>
          </Box>
        </Box>
        <Box component="main" sx={{ p: 1.5, flex: 1, minWidth: 0 }}>
          <LiveBanner polling={polling} />
          <Outlet />
        </Box>
      </Box>
      <ChangePasswordDialog open={mustChangePassword || changePassword} forced={mustChangePassword} onClose={() => setChangePassword(false)} />
    </Box>
  );
}
