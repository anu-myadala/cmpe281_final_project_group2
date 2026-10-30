// StatsPage: filters and KPI tiles (7), alerts by type per period, events by class, device
// status donut, top vehicles (8), Run / Export CSV (10). GET /dashboard/stats; alert.created
// refreshes the tiles through the QueryCache.
import { useMemo, useState } from 'react';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import TextField from '@mui/material/TextField';
import Select from '@mui/material/Select';
import MenuItem from '@mui/material/MenuItem';
import FormControl from '@mui/material/FormControl';
import InputLabel from '@mui/material/InputLabel';
import Typography from '@mui/material/Typography';
import Alert from '@mui/material/Alert';
import { useAuth } from '../auth/AuthProvider';
import { useReport, useStats } from '../api/queries';
import { ApiClient, errorMessage, isApiError } from '../api/ApiClient';
import { Card, DataTable, FilterBar, KpiTile, Loading, type Column } from '../components/common';
import { ChartCard, Donut, SimpleBars, StackedBars } from '../components/ChartCard';
import { StatusChip } from '../components/StatusChip';
import { useToast } from '../components/Toast';
import { daysAgo, formatNumber, formatPercent, formatShortTime } from '../util/format';
import { CONNECTION_COLORS, SEVERITY_COLORS } from '../theme';

const ALERT_TYPES = ['EMERGENCY', 'ACCIDENT', 'SECURITY', 'SAFETY', 'PASSENGER', 'VEHICLE_HEALTH'];
const ALERT_TYPE_COLORS: Record<string, string> = {
  EMERGENCY: SEVERITY_COLORS.HIGH,
  ACCIDENT: SEVERITY_COLORS.CRITICAL,
  SECURITY: '#2563eb',
  SAFETY: SEVERITY_COLORS.MEDIUM,
  PASSENGER: '#93b4f5',
  VEHICLE_HEALTH: '#c9c7c0',
};
const DEVICE_COLORS: Record<string, string> = {
  ONLINE: CONNECTION_COLORS.ONLINE,
  OFFLINE: CONNECTION_COLORS.OFFLINE,
  FAULT: CONNECTION_COLORS.FAULT,
  PROVISIONING: '#93b4f5',
  DISABLED: '#c9c7c0',
};

type VehicleRow = { vehicleId: string; label: string; status: string; events: number; topClass: string; lastEventAt: string; alerts: number; openAlerts: number; falseAlarms: number };

export function StatsPage() {
  const { tenantId, isStaff } = useAuth();
  const { toast } = useToast();
  const [from, setFrom] = useState(daysAgo(7));
  const [to, setTo] = useState(daysAgo(0));
  const [granularity, setGranularity] = useState('auto');
  const [applied, setApplied] = useState({ from: daysAgo(7), to: daysAgo(0), granularity: 'auto' });
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const fromIso = `${applied.from}T00:00:00Z`;
  const toIso = `${applied.to}T23:59:59Z`;
  const stats = useStats(tenantId, fromIso, toIso, applied.granularity);
  const activity = useReport('vehicle-activity', { from: fromIso, to: toIso, tenantId }, true);

  const exportCsv = async () => {
    setExporting(true);
    setExportError(null);
    try {
      const name = await ApiClient.download('/reports/alerts-by-day', { from: fromIso, to: toIso, tenantId, format: 'csv' }, `alerts-by-day_${applied.from}_${applied.to}.csv`);
      toast(`Export ready: ${name}`);
    } catch (error) {
      setExportError(errorMessage(error));
    } finally {
      setExporting(false);
    }
  };

  const perPeriod = useMemo(
    () =>
      (stats.data?.alertsByTypePerPeriod ?? []).map((row) => {
        const period = String(row.period);
        const label = stats.data?.granularity === 'hour' ? period.slice(5, 16).replace('T', ' ') : period.slice(5, 10);
        return { ...row, label };
      }),
    [stats.data],
  );
  const eventsByClass = stats.data?.eventsByClass ?? [];
  const deviceStatus = stats.data?.deviceStatus ?? {};
  const deviceDegraded = Boolean(deviceStatus.degraded);
  const donutData = Object.entries(deviceStatus)
    .filter(([key, value]) => key !== 'degraded' && typeof value === 'number')
    .map(([name, value]) => ({ name, value: value as number }));
  const deviceTotal = donutData.reduce((sum, d) => sum + d.value, 0);

  const topVehicles: VehicleRow[] = useMemo(() => {
    if (!activity.data) return [];
    const cols = activity.data.columns;
    return activity.data.rows
      .map((values) => Object.fromEntries(cols.map((c, i) => [c, values[i]])) as unknown as VehicleRow)
      .sort((a, b) => Number(b.alerts ?? 0) - Number(a.alerts ?? 0))
      .slice(0, 8);
  }, [activity.data]);

  const rangeError = stats.error && isApiError(stats.error) && stats.error.rule === 'BR-08' ? stats.error.message : null;

  if (tenantId === null && isStaff) return <Alert severity="info">Select a tenant in the top bar to see its statistics.</Alert>;

  const vehicleColumns: Column<VehicleRow>[] = [
    { key: 'vehicleId', header: 'Vehicle', render: (r) => <b>{r.vehicleId}</b> },
    { key: 'status', header: 'Status', render: (r) => <StatusChip value={r.status} /> },
    { key: 'alerts', header: 'Alerts', align: 'right' },
    { key: 'openAlerts', header: 'Open', align: 'right' },
    { key: 'events', header: 'Events', align: 'right' },
    { key: 'topClass', header: 'Top class', render: (r) => r.topClass ?? '-' },
    { key: 'lastEventAt', header: 'Last event', render: (r) => formatShortTime(r.lastEventAt) },
  ];

  const online = Number(deviceStatus.ONLINE ?? 0);
  return (
    <Box>
      <FilterBar
        trailing={
          <>
            <Button variant="contained" onClick={() => setApplied({ from, to, granularity })} data-testid="stats-run">
              Run
            </Button>
            <Button variant="outlined" onClick={() => void exportCsv()} disabled={exporting} data-testid="stats-export">
              Export CSV
            </Button>
          </>
        }
      >
        <TextField label="From" type="date" value={from} onChange={(event) => setFrom(event.target.value)} InputLabelProps={{ shrink: true }} sx={{ width: 150 }} />
        <TextField label="To" type="date" value={to} onChange={(event) => setTo(event.target.value)} InputLabelProps={{ shrink: true }} sx={{ width: 150 }} error={Boolean(rangeError)} helperText={rangeError ?? undefined} />
        <FormControl sx={{ minWidth: 130 }}>
          <InputLabel id="granularity">Granularity</InputLabel>
          <Select labelId="granularity" label="Granularity" value={granularity} onChange={(event) => setGranularity(event.target.value)}>
            <MenuItem value="auto">auto{stats.data ? ` (${stats.data.granularity})` : ''}</MenuItem>
            <MenuItem value="hour">hour</MenuItem>
            <MenuItem value="day">day</MenuItem>
          </Select>
        </FormControl>
        <Typography variant="caption" sx={{ pb: 0.75 }}>
          GET /dashboard/stats {'·'} up to 2 days by hour, up to 92 days by day (BR-08)
        </Typography>
      </FilterBar>
      {exportError && (
        <Alert severity="error" sx={{ mb: 1, py: 0, fontSize: 12 }}>
          {exportError}
        </Alert>
      )}
      {stats.isLoading ? (
        <Loading label="Loading statistics" />
      ) : stats.error && !rangeError ? (
        <Alert severity="error">{errorMessage(stats.error)}</Alert>
      ) : stats.data ? (
        <>
          <Box sx={{ display: 'flex', gap: 1.25, mb: 1.25, flexWrap: 'wrap' }} data-testid="stats-kpis">
            <KpiTile label="Devices online" value={deviceDegraded ? 'n/a' : `${online} of ${deviceTotal}`} hint={deviceDegraded ? 'C2 unavailable' : undefined} />
            <KpiTile label="Open alerts" value={stats.data.totals.open} />
            <KpiTile label="Alerts in range" value={formatNumber(stats.data.totals.alerts)} />
            <KpiTile label="Events in range" value={formatNumber(stats.data.totals.events)} />
            <KpiTile label="False-alarm rate" value={formatPercent(stats.data.falseAlarmRate)} />
            <KpiTile label="Mean ack time" value={stats.data.meanAckSeconds === null ? '-' : `${Math.round(stats.data.meanAckSeconds)} s`} />
          </Box>
          <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: '1.3fr 1fr' }, gap: 1.25, mb: 1.25 }}>
            <ChartCard title={`Alerts by type per ${stats.data.granularity}`} note="alertsByTypePerPeriod" empty={perPeriod.length === 0}>
              <StackedBars data={perPeriod as Array<Record<string, number | string>>} keys={ALERT_TYPES.filter((t) => perPeriod.some((row) => Number((row as Record<string, unknown>)[t] ?? 0) > 0))} xKey="label" colorsByKey={ALERT_TYPE_COLORS} />
            </ChartCard>
            <ChartCard title="Events by class" note="eventsByClass" empty={eventsByClass.length === 0}>
              <SimpleBars data={eventsByClass.map((e) => ({ class: e.class, count: e.count }))} valueKey="count" xKey="class" />
            </ChartCard>
          </Box>
          <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: '1fr 1.6fr' }, gap: 1.25 }}>
            <ChartCard title="Device status" note={deviceDegraded ? 'C2 summary not available' : 'deviceStatus (C2 summary)'} height={160} empty={deviceDegraded || deviceTotal === 0}>
              <Donut data={donutData} colorsByName={DEVICE_COLORS} />
            </ChartCard>
            <Card title="Top vehicles by alerts" note="vehicle-activity report">
              {activity.isLoading ? <Loading /> : <DataTable columns={vehicleColumns} rows={topVehicles} rowKey={(r) => r.vehicleId} empty="No vehicle activity in this range" dense />}
            </Card>
          </Box>
        </>
      ) : null}
    </Box>
  );
}
