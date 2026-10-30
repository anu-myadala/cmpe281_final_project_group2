// ReportsPage: filter card (7), chart and the rows of the report (8), Run report / Export CSV (10).
// GET /reports/{type}?from&to&vehicleId&format=json|csv; 422 BR-08 / BR-09 shown inline.
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
import { useLiveEvents } from '../live/useLiveEvents';
import { useOverview, useReport } from '../api/queries';
import { ApiClient, errorMessage, isApiError } from '../api/ApiClient';
import { Card, DataTable, FilterBar, Loading, type Column } from '../components/common';
import { ChartCard, SimpleBars, StackedBars } from '../components/ChartCard';
import { useToast } from '../components/Toast';
import { daysAgo } from '../util/format';
import { SEVERITY_COLORS } from '../theme';

const REPORT_TYPES = [
  { value: 'alerts-by-day', label: 'Alerts by day' },
  { value: 'events-by-class', label: 'Events by class' },
  { value: 'vehicle-activity', label: 'Vehicle activity' },
  { value: 'false-alarms', label: 'False alarms' },
];

const ALERT_TYPE_COLORS: Record<string, string> = {
  EMERGENCY: SEVERITY_COLORS.HIGH,
  ACCIDENT: SEVERITY_COLORS.CRITICAL,
  SECURITY: '#2563eb',
  SAFETY: SEVERITY_COLORS.MEDIUM,
  PASSENGER: '#93b4f5',
  VEHICLE_HEALTH: '#c9c7c0',
};

type Row = Record<string, string | number | null>;

export function ReportsPage() {
  const { tenantId, isStaff } = useAuth();
  const { polling } = useLiveEvents();
  const { toast } = useToast();
  const overview = useOverview(tenantId, polling);
  const [type, setType] = useState('alerts-by-day');
  const [from, setFrom] = useState(daysAgo(7));
  const [to, setTo] = useState(daysAgo(0));
  const [vehicleId, setVehicleId] = useState('');
  const [applied, setApplied] = useState<{ type: string; from: string; to: string; vehicleId?: string } | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const report = useReport(applied?.type ?? type, { from: `${applied?.from ?? from}T00:00:00Z`, to: `${applied?.to ?? to}T23:59:59Z`, vehicleId: applied?.vehicleId, tenantId }, applied !== null);

  const run = () => {
    setExportError(null);
    setApplied({ type, from, to, vehicleId: vehicleId || undefined });
  };

  const exportCsv = async () => {
    setExporting(true);
    setExportError(null);
    try {
      const name = await ApiClient.download(`/reports/${type}`, { from: `${from}T00:00:00Z`, to: `${to}T23:59:59Z`, vehicleId: vehicleId || undefined, tenantId, format: 'csv' }, `${type}_${from}_${to}.csv`);
      toast(`Export ready: ${name}`);
    } catch (error) {
      setExportError(errorMessage(error));
    } finally {
      setExporting(false);
    }
  };

  const rows: Row[] = useMemo(() => {
    if (!report.data) return [];
    return report.data.rows.map((values) => Object.fromEntries(report.data!.columns.map((col, index) => [col, values[index] ?? null])));
  }, [report.data]);

  const columns: Column<Row>[] = (report.data?.columns ?? []).map((col) => ({ key: col, header: col, render: (row) => formatCell(row[col]) }));
  const rangeError = report.error && isApiError(report.error) && report.error.rule === 'BR-08' ? report.error.message : null;

  const chart = useMemo(() => {
    if (!report.data) return null;
    const columnsOf = report.data.columns;
    if (report.data.type === 'alerts-by-day') {
      const keys = columnsOf.filter((c) => c !== 'day' && c !== 'total');
      return <StackedBars data={rows.map((r) => ({ ...r, day: String(r.day ?? '').slice(5) })) as Array<Record<string, number | string>>} keys={keys} xKey="day" colorsByKey={ALERT_TYPE_COLORS} />;
    }
    if (report.data.type === 'events-by-class') return <SimpleBars data={rows as Array<Record<string, number | string>>} valueKey="count" xKey="class" />;
    if (report.data.type === 'vehicle-activity') return <SimpleBars data={rows as Array<Record<string, number | string>>} valueKey="alerts" xKey="vehicleId" />;
    const byType: Record<string, number> = {};
    rows.forEach((r) => {
      const key = String(r.type ?? 'other');
      byType[key] = (byType[key] ?? 0) + 1;
    });
    return <SimpleBars data={Object.entries(byType).map(([t, count]) => ({ type: t, count }))} valueKey="count" xKey="type" color={SEVERITY_COLORS.LOW} />;
  }, [report.data, rows]);

  if (tenantId === null && isStaff) return <Alert severity="info">Select a tenant in the top bar to run its reports.</Alert>;

  const vehicles = overview.data?.vehicles ?? [];
  const typeLabel = REPORT_TYPES.find((t) => t.value === (applied?.type ?? type))?.label ?? type;

  return (
    <Box>
      <Card title="Report" sx={{ mb: 1.25 }}>
        <FilterBar sx={{ border: 'none', p: 0, mb: 0.5 }}>
          <FormControl sx={{ minWidth: 170 }}>
            <InputLabel id="report-type">Report type</InputLabel>
            <Select labelId="report-type" label="Report type" value={type} onChange={(event) => setType(event.target.value)} data-testid="report-type">
              {REPORT_TYPES.map((t) => (
                <MenuItem key={t.value} value={t.value}>
                  {t.value}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <TextField label="From" type="date" value={from} onChange={(event) => setFrom(event.target.value)} InputLabelProps={{ shrink: true }} sx={{ width: 150 }} />
          <TextField label="To" type="date" value={to} onChange={(event) => setTo(event.target.value)} InputLabelProps={{ shrink: true }} sx={{ width: 150 }} error={Boolean(rangeError)} helperText={rangeError ?? undefined} />
          <FormControl sx={{ minWidth: 170 }}>
            <InputLabel id="report-vehicle">Vehicle</InputLabel>
            <Select labelId="report-vehicle" label="Vehicle" value={vehicleId} onChange={(event) => setVehicleId(event.target.value)} displayEmpty>
              <MenuItem value="">All vehicles{vehicles.length ? ` (${vehicles.length})` : ''}</MenuItem>
              {vehicles.map((v) => (
                <MenuItem key={v.vehicleId} value={v.vehicleId}>
                  {v.vehicleId}
                  {v.label ? ` · ${v.label}` : ''}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <Box sx={{ display: 'flex', gap: 0.75, alignItems: 'center', pb: 0.25 }}>
            <Button variant="contained" onClick={run} data-testid="run-report">
              Run report
            </Button>
            <Button variant="outlined" onClick={() => void exportCsv()} disabled={exporting} data-testid="export-csv">
              Export CSV
            </Button>
            <Button variant="outlined" disabled sx={{ borderStyle: 'dashed' }}>
              Export PDF (design only)
            </Button>
          </Box>
        </FilterBar>
        {exportError && (
          <Typography sx={{ color: '#b42318', fontSize: 12 }} data-testid="export-error">
            {exportError}
          </Typography>
        )}
        <Typography variant="caption">
          types: alerts-by-day, events-by-class, vehicle-activity, false-alarms {'·'} range up to 92 days (BR-08) {'·'} CSV up to 50,000 rows (BR-09)
        </Typography>
      </Card>
      {applied && (
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: '1fr 1.15fr' }, gap: 1.25, alignItems: 'start' }}>
          <ChartCard title={`${typeLabel}${overview.data ? ` - ${overview.data.tenant.name}` : ''}`} note={`(${applied.from} to ${applied.to})`} empty={!report.isLoading && rows.length === 0} height={220}>
            {report.isLoading ? <Loading /> : chart}
          </ChartCard>
          <Card title="Rows" note={report.data ? `format=json: ${report.data.columns.length} columns, total = ${report.data.total}` : ''} testId="report-rows">
            {report.isLoading ? <Loading /> : report.error && !rangeError ? <Alert severity="error">{errorMessage(report.error)}</Alert> : <DataTable columns={columns} rows={rows} rowKey={(row) => JSON.stringify(row)} maxHeight={420} empty="No rows in this range" />}
            <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
              same columns in the CSV (text/csv, attachment)
            </Typography>
          </Card>
        </Box>
      )}
    </Box>
  );
}

function formatCell(value: string | number | null): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(3);
  return value;
}
