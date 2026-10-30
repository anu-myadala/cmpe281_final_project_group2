// HostedPage: placeholders for the pages of C2 / C3 (alert rules, devices, provisioning) and
// the design-only admin pages (audit log, platform health). They name the owning component
// and, where cheap, probe its API so the demo shows whether the component is up.
import { useQuery } from '@tanstack/react-query';
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';
import Alert from '@mui/material/Alert';
import { useAuth } from '../auth/AuthProvider';
import { ApiClient, C2_BASE, C3_BASE, c2, c3 } from '../api/ApiClient';
import { Card, DataTable, Loading, Unavailable } from '../components/common';
import { StatusChip } from '../components/StatusChip';
import { HostedNote } from './AlertMonitorPage';

type PageKey = 'alert-rules' | 'devices' | 'provisioning' | 'audit' | 'health';

const PAGES: Record<PageKey, { title: string; component: 'C2' | 'C3' | 'C1'; text: string; path?: string }> = {
  'alert-rules': { title: 'Alert rules', component: 'C3', text: 'Rule thresholds per sound class and vehicle state (Part III, Section 2.2). The rule editor is a C3 page; this view lists the rules the stub exposes.', path: '/alert-rules' },
  devices: { title: 'Devices', component: 'C2', text: 'Device registry of the vehicles (Part II). The device pages are hosted by C2; this view lists the vehicles with their device summary.', path: '/vehicles' },
  provisioning: { title: 'Provisioning', component: 'C2', text: 'Device provisioning and certificate issue (Part II). Hosted by C2.' },
  audit: { title: 'Audit log', component: 'C1', text: 'Design only this semester (FR-C1-06): every write is stored in audit_log with the before and after values (BR-12).' },
  health: { title: 'Platform health', component: 'C1', text: 'Design only this semester (FR-C1-06). The API health endpoint is probed below.' },
};

interface Rule {
  ruleId: number;
  scope?: string;
  tenantId?: number | null;
  soundClass?: string;
  class?: string;
  alertType?: string;
  severity?: string;
  minConfidence?: number;
  vehicleState?: string;
  enabled?: boolean;
}

export function HostedPage({ page }: { page: PageKey }) {
  const { tenantId } = useAuth();
  const meta = PAGES[page];
  const probe = useQuery<unknown>({
    queryKey: ['hosted', page, tenantId],
    queryFn: async () => {
      if (page === 'alert-rules') return c3.get<unknown>('/alert-rules', { tenantId });
      if (page === 'devices') return c2.get<unknown>('/vehicles', { tenantId, size: 100 });
      if (page === 'health') return ApiClient.get<unknown>('/health');
      return null;
    },
    retry: false,
    enabled: page === 'alert-rules' || page === 'devices' || page === 'health',
  });

  const base = meta.component === 'C2' ? C2_BASE : meta.component === 'C3' ? C3_BASE : '/api/v1';
  return (
    <Box>
      {meta.component !== 'C1' && <HostedNote component={meta.component} what={meta.title} />}
      <Card title={meta.title} note={meta.path ? `${base}${meta.path}` : undefined} testId={`hosted-${page}`}>
        <Typography sx={{ fontSize: 12.5, mb: 1 }}>{meta.text}</Typography>
        {page === 'alert-rules' && (probe.isLoading ? <Loading /> : probe.error ? <Unavailable what="The alert rule service (C3)" error={probe.error} /> : <RulesTable data={probe.data} />)}
        {page === 'devices' && (probe.isLoading ? <Loading /> : probe.error ? <Unavailable what="The vehicle service (C2)" error={probe.error} /> : <DevicesSummary data={probe.data} />)}
        {page === 'health' && (probe.isLoading ? <Loading /> : probe.error ? <Unavailable what="GET /api/v1/health" error={probe.error} /> : <pre style={{ fontSize: 11, background: '#f5f4f0', padding: 8, borderRadius: 5, margin: 0 }}>{JSON.stringify(probe.data, null, 2)}</pre>)}
        {(page === 'provisioning' || page === 'audit') && (
          <Alert severity="info" variant="outlined" sx={{ fontSize: 12, borderStyle: 'dashed' }}>
            {page === 'provisioning' ? 'Provisioning runs in the C2 console; nothing to call from here.' : 'The audit log is written by AuditLogger; a reader page is design only.'}
          </Alert>
        )}
      </Card>
    </Box>
  );
}

function RulesTable({ data }: { data: unknown }) {
  const rows: Rule[] = Array.isArray(data) ? (data as Rule[]) : ((data as { items?: Rule[] } | null)?.items ?? []);
  return (
    <DataTable
      columns={[
        { key: 'ruleId', header: 'Rule' },
        { key: 'scope', header: 'Scope', render: (r) => r.scope ?? (r.tenantId ? `tenant ${r.tenantId}` : 'DEFAULT') },
        { key: 'class', header: 'Sound class', render: (r) => r.soundClass ?? r.class ?? '-' },
        { key: 'alertType', header: 'Alert type', render: (r) => r.alertType ?? '-' },
        { key: 'severity', header: 'Severity', render: (r) => (r.severity ? <StatusChip value={r.severity} /> : '-') },
        { key: 'minConfidence', header: 'Min conf.', render: (r) => (typeof r.minConfidence === 'number' ? r.minConfidence.toFixed(2) : '-') },
        { key: 'vehicleState', header: 'Vehicle state', render: (r) => r.vehicleState ?? 'ANY' },
        { key: 'enabled', header: 'Enabled', render: (r) => (r.enabled === false ? 'no' : 'yes') },
      ]}
      rows={rows}
      rowKey={(r) => r.ruleId}
      empty="No rules returned"
    />
  );
}

function DevicesSummary({ data }: { data: unknown }) {
  type Row = { vehicleId: string; label?: string | null; connection?: string | null; deviceSummary?: { total: number; online: number; offline: number; fault: number; disabled: number } | null };
  const rows: Row[] = Array.isArray(data) ? (data as Row[]) : ((data as { items?: Row[] } | null)?.items ?? []);
  return (
    <DataTable
      columns={[
        { key: 'vehicleId', header: 'Vehicle', render: (r) => <b>{r.vehicleId}</b> },
        { key: 'label', header: 'Label', render: (r) => r.label ?? '-' },
        { key: 'connection', header: 'Agent', render: (r) => <StatusChip value={r.connection ?? 'NEVER'} /> },
        { key: 'total', header: 'Devices', render: (r) => r.deviceSummary?.total ?? '-' },
        { key: 'online', header: 'Online', render: (r) => r.deviceSummary?.online ?? '-' },
        { key: 'offline', header: 'Offline', render: (r) => r.deviceSummary?.offline ?? '-' },
        { key: 'fault', header: 'Fault', render: (r) => r.deviceSummary?.fault ?? '-' },
        { key: 'disabled', header: 'Disabled', render: (r) => r.deviceSummary?.disabled ?? '-' },
      ]}
      rows={rows}
      rowKey={(r) => r.vehicleId}
      empty="No vehicles returned"
    />
  );
}
