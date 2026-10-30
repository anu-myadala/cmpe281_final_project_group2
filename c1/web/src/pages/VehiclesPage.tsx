// VehiclesPage: the vehicle table from C2 (GET {C2}/vehicles) with the device list of the
// selected vehicle (GET {C2}/vehicles/{id}/devices). The full vehicle pages are hosted by C2;
// when C2 is not reachable the page falls back to the vehicles of the overview.
import { useMemo } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Typography from '@mui/material/Typography';
import Alert from '@mui/material/Alert';
import { useAuth } from '../auth/AuthProvider';
import { useLiveEvents } from '../live/useLiveEvents';
import { useC2Devices, useC2Vehicles, useOverview } from '../api/queries';
import type { C2Vehicle } from '../api/types';
import { Card, DataTable, Loading, Unavailable, type Column } from '../components/common';
import { StatusChip } from '../components/StatusChip';
import { C2_BASE } from '../api/ApiClient';
import { ago, formatDateTime } from '../util/format';

export function VehiclesPage() {
  const { tenantId, isStaff, permissions } = useAuth();
  const { polling } = useLiveEvents();
  const navigate = useNavigate();
  const { vehicleId } = useParams();
  const [params] = useSearchParams();
  const q = (params.get('q') ?? '').toLowerCase();
  const c2 = useC2Vehicles(tenantId);
  const overview = useOverview(c2.isError ? tenantId : null, polling);

  const rows: C2Vehicle[] = useMemo(() => {
    const items: C2Vehicle[] = c2.data?.items ?? (overview.data?.vehicles.map((v) => ({ ...v, make: v.make ?? null, model: v.model ?? null, year: v.year ?? null, vin: v.vin ?? null, label: v.label ?? null, deviceSummary: v.deviceSummary })) ?? []);
    return q ? items.filter((v) => `${v.vehicleId} ${v.vin ?? ''} ${v.label ?? ''} ${v.make ?? ''} ${v.model ?? ''}`.toLowerCase().includes(q)) : items;
  }, [c2.data, overview.data, q]);

  const selected = rows.find((v) => v.vehicleId === vehicleId) ?? null;
  const devices = useC2Devices(selected ? selected.vehicleId : null, tenantId);

  if (tenantId === null && isStaff) return <Alert severity="info">Select a tenant in the top bar to list its vehicles.</Alert>;

  const columns: Column<C2Vehicle>[] = [
    { key: 'vehicleId', header: 'Vehicle', render: (v) => <b>{v.vehicleId}</b> },
    { key: 'label', header: 'Label', render: (v) => v.label ?? '-' },
    { key: 'vin', header: 'VIN', render: (v) => v.vin ?? '-' },
    { key: 'model', header: 'Make / model', render: (v) => [v.make, v.model, v.year].filter(Boolean).join(' ') || '-' },
    { key: 'status', header: 'Status', render: (v) => <StatusChip value={v.status} /> },
    { key: 'connection', header: 'Agent', render: (v) => <StatusChip value={v.connection ?? 'NEVER'} /> },
    { key: 'lastSeen', header: 'Last seen', render: (v) => (v.lastSeenAt ? `${ago(v.lastSeenAt)} ago` : 'never') },
    { key: 'devices', header: 'Devices', render: (v) => (v.deviceSummary ? `${v.deviceSummary.online}/${v.deviceSummary.total} online${v.deviceSummary.fault ? `, ${v.deviceSummary.fault} fault` : ''}` : '-') },
  ];

  return (
    <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 1.7fr) minmax(300px, 1fr)' }, gap: 1.25, alignItems: 'start' }}>
      <Card title="Vehicles" note={c2.isError ? 'from the overview (C2 not reachable)' : `GET ${C2_BASE}/vehicles`} testId="vehicles-table-card">
        {c2.isError && <Box sx={{ mb: 1 }}><Unavailable what="The vehicle service (C2)" error={c2.error} compact /></Box>}
        {c2.isLoading || (c2.isError && overview.isLoading) ? (
          <Loading label="Loading vehicles" />
        ) : (
          <DataTable columns={columns} rows={rows} rowKey={(v) => v.vehicleId} selectedKey={vehicleId ?? null} onRowClick={(v) => navigate(`/vehicles/${v.vehicleId}`)} empty={q ? `No vehicle matches "${q}"` : 'No vehicles'} testId="vehicles-table" />
        )}
        <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
          Vehicle detail, clips and device provisioning are pages of C2 hosted in this shell (*). Click a row for the device summary.
        </Typography>
      </Card>
      <Card title={selected ? `${selected.vehicleId}${selected.label ? ` · ${selected.label}` : ''}` : 'Vehicle detail'} testId="vehicle-detail">
        {selected ? (
          <Box sx={{ fontSize: 12, lineHeight: 1.8 }}>
            <StatusChip value={selected.status} /> <StatusChip value={selected.connection ?? 'NEVER'} />
            <br />
            VIN {selected.vin ?? '-'} {'·'} {[selected.make, selected.model, selected.year].filter(Boolean).join(' ') || 'make unknown'}
            <br />
            last seen {formatDateTime(selected.lastSeenAt)}
            {selected.retentionDays ? ` · clip retention ${selected.retentionDays} d` : ''}
            <Typography variant="subtitle2" sx={{ mt: 1 }}>
              Devices
            </Typography>
            {devices.isLoading ? (
              <Loading />
            ) : devices.error ? (
              <Unavailable what="The device list (C2)" error={devices.error} compact />
            ) : (
              <DataTable
                columns={[
                  { key: 'deviceId', header: 'Device' },
                  { key: 'type', header: 'Type' },
                  { key: 'status', header: 'Status', render: (d) => <StatusChip value={d.status} /> },
                  { key: 'lastSeenAt', header: 'Last seen', render: (d) => (d.lastSeenAt ? `${ago(d.lastSeenAt)} ago` : '-') },
                ]}
                rows={devices.data ?? []}
                rowKey={(d) => d.deviceId}
                empty="No devices"
                dense
              />
            )}
            <Box sx={{ mt: 1, display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
              {permissions.includes('map') && (
                <Button variant="outlined" onClick={() => navigate(`/map?vehicle=${selected.vehicleId}`)}>
                  On map
                </Button>
              )}
              {permissions.includes('alerts') && (
                <Button variant="outlined" onClick={() => navigate(`/alerts?vehicle=${selected.vehicleId}`)}>
                  Alerts *
                </Button>
              )}
              {permissions.includes('subscriptions') && (
                <Button variant="outlined" onClick={() => navigate(`/subscriptions?vehicle=${selected.vehicleId}`)}>
                  Subscriptions
                </Button>
              )}
            </Box>
          </Box>
        ) : (
          <Typography variant="caption">Select a vehicle in the table.</Typography>
        )}
      </Card>
    </Box>
  );
}
