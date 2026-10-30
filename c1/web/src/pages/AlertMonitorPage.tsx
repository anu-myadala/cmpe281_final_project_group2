// AlertMonitorPage / AlertDetailPage: the C3 alert pages hosted in the shell. The list reads
// GET {C3}/alerts and posts Acknowledge / Resolve / False alarm actions; the detail reads
// GET {C3}/alerts/{id}. Without C3 the pages show a graceful "not available" state.
import { useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Typography from '@mui/material/Typography';
import Select from '@mui/material/Select';
import MenuItem from '@mui/material/MenuItem';
import Alert from '@mui/material/Alert';
import { useAuth } from '../auth/AuthProvider';
import { useLiveEvents } from '../live/useLiveEvents';
import { useC3Alerts, useOverview } from '../api/queries';
import { C3_BASE, c3 } from '../api/ApiClient';
import type { AlertRow } from '../api/types';
import { Card, DataTable, FilterBar, Loading, Unavailable, type Column } from '../components/common';
import { StatusChip } from '../components/StatusChip';
import { useAlertAction } from '../components/RecentAlertsTable';
import { formatDateTime, formatShortTime } from '../util/format';

export function AlertMonitorPage() {
  const { tenantId, isStaff, user } = useAuth();
  const { polling, highlighted, liveAlerts } = useLiveEvents();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState('OPEN');
  const vehicleFilter = params.get('vehicle') ?? '';
  const alerts = useC3Alerts(tenantId, status, polling);
  const overview = useOverview(alerts.isError ? tenantId : null, polling);
  const { run, busyId } = useAlertAction(tenantId);
  const canAct = user?.role !== 'RIDER' && user?.role !== 'IOT_ENGINEER';

  const rows = useMemo(() => {
    const base: AlertRow[] = alerts.data?.items ?? (alerts.isError ? overview.data?.recentAlerts ?? [] : []);
    const known = new Set(base.map((a) => a.alertId));
    const merged = [...liveAlerts.filter((a) => !known.has(a.alertId)), ...base];
    return vehicleFilter ? merged.filter((a) => a.vehicleId === vehicleFilter) : merged;
  }, [alerts.data, alerts.isError, overview.data, liveAlerts, vehicleFilter]);

  if (tenantId === null && isStaff) return <Alert severity="info">Select a tenant in the top bar to see its alerts.</Alert>;

  const columns: Column<AlertRow>[] = [
    { key: 'alertId', header: 'Id' },
    { key: 'time', header: 'Time', render: (a) => formatShortTime(a.createdAt) },
    { key: 'vehicleId', header: 'Vehicle' },
    { key: 'type', header: 'Type' },
    { key: 'severity', header: 'Sev.', render: (a) => <StatusChip value={a.severity} /> },
    { key: 'topClass', header: 'Class', render: (a) => a.topClass ?? '-' },
    { key: 'confidence', header: 'Conf.', render: (a) => (a.confidence !== null && a.confidence !== undefined ? a.confidence.toFixed(2) : '-') },
    { key: 'status', header: 'Status', render: (a) => <StatusChip value={a.status} /> },
    {
      key: 'actions',
      header: 'Actions',
      render: (a) => (
        <Box sx={{ display: 'flex', gap: 0.5 }} onClick={(event) => event.stopPropagation()}>
          {canAct && a.status === 'NEW' && (
            <Button variant="contained" size="small" sx={{ py: 0.1, px: 1, fontSize: 11.5 }} disabled={busyId === a.alertId} onClick={() => void run(a.alertId, 'ACKNOWLEDGE')}>
              Acknowledge
            </Button>
          )}
          {canAct && (a.status === 'NEW' || a.status === 'ACKNOWLEDGED') && (
            <>
              <Button variant="outlined" size="small" sx={{ py: 0.1, px: 1, fontSize: 11.5 }} disabled={busyId === a.alertId} onClick={() => void run(a.alertId, 'RESOLVE')}>
                Resolve
              </Button>
              <Button variant="outlined" size="small" sx={{ py: 0.1, px: 1, fontSize: 11.5 }} disabled={busyId === a.alertId} onClick={() => void run(a.alertId, 'FALSE_ALARM')}>
                False alarm
              </Button>
            </>
          )}
          <Button variant="outlined" size="small" sx={{ py: 0.1, px: 1, fontSize: 11.5 }} onClick={() => navigate(`/alerts/${a.alertId}`)}>
            Open
          </Button>
        </Box>
      ),
    },
  ];

  return (
    <Box>
      <HostedNote component="C3" what="Alert monitor" />
      <FilterBar>
        <Select value={status} onChange={(event) => setStatus(event.target.value)} sx={{ height: 28, fontSize: 12, minWidth: 150 }} data-testid="alert-status-filter">
          <MenuItem value="OPEN">Status: open (NEW, ACKNOWLEDGED)</MenuItem>
          <MenuItem value="NEW">Status: NEW</MenuItem>
          <MenuItem value="ACKNOWLEDGED">Status: ACKNOWLEDGED</MenuItem>
          <MenuItem value="RESOLVED">Status: RESOLVED</MenuItem>
          <MenuItem value="FALSE_ALARM">Status: FALSE_ALARM</MenuItem>
          <MenuItem value="ALL">Status: all</MenuItem>
        </Select>
        {vehicleFilter && (
          <Button
            variant="outlined"
            onClick={() => {
              const next = new URLSearchParams(params);
              next.delete('vehicle');
              setParams(next);
            }}
          >
            Vehicle: {vehicleFilter} (clear)
          </Button>
        )}
        <Typography variant="caption" sx={{ pb: 0.5 }}>
          GET {C3_BASE}/alerts {'·'} live via alert.created / alert.updated
        </Typography>
      </FilterBar>
      <Card title="Alerts" note={alerts.data ? `${alerts.data.total} total` : alerts.isError ? 'from the overview (C3 not reachable)' : ''} testId="alerts-card">
        {alerts.isError && <Box sx={{ mb: 1 }}><Unavailable what="The alert service (C3)" error={alerts.error} compact /></Box>}
        {alerts.isLoading ? <Loading label="Loading alerts" /> : <DataTable columns={columns} rows={rows} rowKey={(a) => a.alertId} highlightedKeys={highlighted} onRowClick={(a) => navigate(`/alerts/${a.alertId}`)} empty="No alerts" testId="alerts-table" />}
      </Card>
    </Box>
  );
}

interface AlertDetail {
  alert: AlertRow & { lat?: number; lon?: number; vehicleState?: string; count?: number };
  event?: { eventRef: string; ts: string; channel?: string; topK?: Array<{ class: string; conf: number }>; modelVersion?: string; latencyMs?: number };
  rule?: { ruleId: number; scope: string; minConfidence: number; vehicleState?: string };
  clip?: { url: string; expiresAt: string; durationMs: number } | null;
  actions?: Array<{ action: string; by?: string; at?: string; note?: string }>;
  notifications?: Array<{ recipient: string; channel: string; status: string }>;
}

export function AlertDetailPage() {
  const { alertId } = useParams();
  const { tenantId, user } = useAuth();
  const navigate = useNavigate();
  const { run, busyId } = useAlertAction(tenantId);
  const canAct = user?.role !== 'RIDER' && user?.role !== 'IOT_ENGINEER';
  const detail = useQuery<AlertDetail>({
    queryKey: ['c3', 'alert', alertId, tenantId],
    queryFn: async () => {
      const data = await c3.get<AlertDetail | AlertDetail['alert']>(`/alerts/${alertId}`, { tenantId });
      return 'alert' in data ? (data as AlertDetail) : { alert: data as AlertDetail['alert'] };
    },
    retry: false,
    enabled: Boolean(alertId),
  });
  const alert = detail.data?.alert;
  return (
    <Box>
      <HostedNote component="C3" what="Alert detail" />
      <Box sx={{ mb: 1 }}>
        <Button variant="outlined" onClick={() => navigate(-1)}>
          Back
        </Button>
      </Box>
      <Card title={`Alert ${alertId}`} note={alert ? `${alert.type} on ${alert.vehicleId}` : ''} testId="alert-detail">
        {detail.isLoading ? (
          <Loading />
        ) : detail.error || !alert ? (
          <Unavailable what={`Alert ${alertId} (C3 ${C3_BASE}/alerts/${alertId})`} error={detail.error} />
        ) : (
          <Box sx={{ fontSize: 12.5, lineHeight: 1.9 }}>
            <StatusChip value={alert.severity} /> <StatusChip value={alert.status} /> {'·'} {alert.type} {'·'} class <b>{alert.topClass ?? '-'}</b>
            {alert.confidence !== null && alert.confidence !== undefined ? ` (${alert.confidence.toFixed(2)})` : ''}
            <br />
            vehicle <b>{alert.vehicleId}</b>
            {alert.vehicleState ? ` · ${alert.vehicleState}` : ''} {'·'} created {formatDateTime(alert.createdAt)}
            {typeof alert.lat === 'number' && typeof alert.lon === 'number' ? ` · at ${alert.lat.toFixed(4)}, ${alert.lon.toFixed(4)}` : ''}
            {alert.acknowledgedBy ? ` · acknowledged by ${alert.acknowledgedBy}` : ''}
            {detail.data?.event && (
              <>
                <br />
                event {detail.data.event.eventRef} {'·'} {formatDateTime(detail.data.event.ts)}
                {detail.data.event.modelVersion ? ` · model ${detail.data.event.modelVersion}` : ''}
                {detail.data.event.topK ? ` · top classes: ${detail.data.event.topK.map((k) => `${k.class} ${k.conf.toFixed(2)}`).join(', ')}` : ''}
              </>
            )}
            {detail.data?.clip?.url && (
              <Box sx={{ mt: 1 }}>
                <audio controls src={detail.data.clip.url} style={{ height: 32 }} />
              </Box>
            )}
            {canAct && (
              <Box sx={{ mt: 1, display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
                {alert.status === 'NEW' && (
                  <Button variant="contained" disabled={busyId === alert.alertId} onClick={() => void run(alert.alertId, 'ACKNOWLEDGE').then((ok) => { if (ok) void detail.refetch(); })}>
                    Acknowledge
                  </Button>
                )}
                {(alert.status === 'NEW' || alert.status === 'ACKNOWLEDGED') && (
                  <>
                    <Button variant="outlined" disabled={busyId === alert.alertId} onClick={() => void run(alert.alertId, 'RESOLVE').then((ok) => { if (ok) void detail.refetch(); })}>
                      Resolve
                    </Button>
                    <Button variant="outlined" disabled={busyId === alert.alertId} onClick={() => void run(alert.alertId, 'FALSE_ALARM').then((ok) => { if (ok) void detail.refetch(); })}>
                      False alarm
                    </Button>
                  </>
                )}
                <Button variant="outlined" onClick={() => navigate(`/map?vehicle=${alert.vehicleId}`)}>
                  Show on map
                </Button>
              </Box>
            )}
            {detail.data?.actions && detail.data.actions.length > 0 && (
              <Typography variant="caption" sx={{ display: 'block', mt: 1 }}>
                actions: {detail.data.actions.map((a) => `${a.action}${a.by ? ` by ${a.by}` : ''}${a.at ? ` at ${formatDateTime(a.at)}` : ''}`).join('; ')}
              </Typography>
            )}
          </Box>
        )}
      </Card>
    </Box>
  );
}

export function HostedNote({ component, what }: { component: 'C2' | 'C3'; what: string }) {
  return (
    <Typography variant="caption" sx={{ display: 'block', mb: 1 }} data-testid="hosted-note">
      {what} is a page of {component} hosted in the C1 shell (*); this view calls the {component} API through the configurable base path.
    </Typography>
  );
}
