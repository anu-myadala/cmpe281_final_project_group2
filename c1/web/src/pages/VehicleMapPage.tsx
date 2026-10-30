// VehicleMapPage: filters row (7), the full 2D map with markers, pins and the trail of the
// selected car (8), the car list and the detail of the selected car (9), Center / Follow /
// Open vehicle / Alerts of this car / Subscriptions (10). GET /dashboard/map every 10 s,
// GET {C2}/vehicles/{id}/telemetry?limit=60 for the trail.
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Typography from '@mui/material/Typography';
import Select from '@mui/material/Select';
import MenuItem from '@mui/material/MenuItem';
import FormControlLabel from '@mui/material/FormControlLabel';
import Checkbox from '@mui/material/Checkbox';
import Alert from '@mui/material/Alert';
import { useAuth } from '../auth/AuthProvider';
import { useC2Devices, useTrail, useVehicleMap } from '../api/queries';
import type { MapVehicle } from '../api/types';
import { Card, DataTable, ErrorNote, FilterBar, Loading, Unavailable, type Column } from '../components/common';
import { StatusChip } from '../components/StatusChip';
import { VehicleMap, trailFromSamples } from '../components/VehicleMap';
import { ago, formatTime } from '../util/format';

type CarFilter = 'all' | 'online' | 'offline' | 'alerts';

export function VehicleMapPage() {
  const { tenantId, isStaff, permissions } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const map = useVehicleMap(tenantId);
  const [selectedVehicleId, setSelectedVehicleId] = useState<string | null>(params.get('vehicle'));
  const [follow, setFollow] = useState(false);
  const [showAlerts, setShowAlerts] = useState(true);
  const [showTrail, setShowTrail] = useState(true);
  const [carFilter, setCarFilter] = useState<CarFilter>('all');
  const [centerRequest, setCenterRequest] = useState(0);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const wanted = params.get('vehicle');
    if (wanted && wanted !== selectedVehicleId) setSelectedVehicleId(wanted);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  const select = (vehicleId: string | null) => {
    setSelectedVehicleId(vehicleId);
    const next = new URLSearchParams(params);
    if (vehicleId) next.set('vehicle', vehicleId);
    else next.delete('vehicle');
    setParams(next, { replace: true });
  };

  // Pins follow alert.created / alert.updated at once (useLiveEvents updates the map query);
  // device.status recolors a marker through the refetch the message triggers.

  const vehicles = useMemo(() => map.data?.vehicles ?? [], [map.data]);
  const alerts = useMemo(() => map.data?.alerts ?? [], [map.data]);
  const openByVehicle = useMemo(() => {
    const counts: Record<string, number> = {};
    alerts.forEach((a) => {
      counts[a.vehicleId] = (counts[a.vehicleId] ?? 0) + 1;
    });
    return counts;
  }, [alerts]);

  const filtered = useMemo(
    () =>
      vehicles.filter((v) => {
        if (carFilter === 'online') return v.connection === 'ONLINE';
        if (carFilter === 'offline') return v.connection !== 'ONLINE';
        if (carFilter === 'alerts') return (openByVehicle[v.vehicleId] ?? 0) > 0;
        return true;
      }),
    [vehicles, carFilter, openByVehicle],
  );

  const selected = vehicles.find((v) => v.vehicleId === selectedVehicleId) ?? null;
  const trail = useTrail(showTrail && selected ? selected.vehicleId : null, tenantId);
  const devices = useC2Devices(selected ? selected.vehicleId : null, tenantId);
  const trailPoints = useMemo(() => (trail.data ? trailFromSamples(trail.data) : []), [trail.data]);

  if (tenantId === null && isStaff) return <Alert severity="info">Select a tenant in the top bar to see its vehicles on the map.</Alert>;

  const columns: Column<MapVehicle>[] = [
    {
      key: 'car',
      header: 'Car',
      render: (v) => (
        <>
          <b>{v.vehicleId}</b> {v.label ?? ''}
        </>
      ),
    },
    { key: 'agent', header: 'Agent', render: (v) => <StatusChip value={v.connection ?? (v.status === 'ACTIVE' ? 'NEVER' : v.status)} label={v.connection ?? v.status} /> },
    { key: 'speed', header: 'Speed', render: (v) => (v.speedKph === null ? '–' : `${Math.round(v.speedKph)} km/h`) },
    { key: 'sample', header: 'Sample', render: (v) => ago(v.sampleAt, now) },
    { key: 'alerts', header: 'Open', render: (v) => openByVehicle[v.vehicleId] ?? 0, align: 'right' },
  ];

  return (
    <Box>
      <FilterBar
        trailing={
          <>
            <Typography variant="caption">
              refreshed every 10 s{map.data ? ` · as of ${formatTime(map.data.asOf)}` : ''}
              {map.data?.degraded ? ' · alert pins degraded (C3)' : ''}
            </Typography>
            <Button variant="outlined" onClick={() => void map.refetch()} disabled={map.isFetching}>
              Refresh now
            </Button>
          </>
        }
      >
        <Typography variant="caption">Show</Typography>
        <Select value={carFilter} onChange={(event) => setCarFilter(event.target.value as CarFilter)} sx={{ height: 28, fontSize: 12, minWidth: 140 }} data-testid="car-filter">
          <MenuItem value="all">Cars: all ({vehicles.length})</MenuItem>
          <MenuItem value="online">Cars: online</MenuItem>
          <MenuItem value="offline">Cars: offline / no sample</MenuItem>
          <MenuItem value="alerts">Cars: with open alerts</MenuItem>
        </Select>
        <Select value={showAlerts ? 'on' : 'off'} onChange={(event) => setShowAlerts(event.target.value === 'on')} sx={{ height: 28, fontSize: 12, minWidth: 130 }}>
          <MenuItem value="on">Open alerts: on</MenuItem>
          <MenuItem value="off">Open alerts: off</MenuItem>
        </Select>
        <Select value={showTrail ? 'on' : 'off'} onChange={(event) => setShowTrail(event.target.value === 'on')} sx={{ height: 28, fontSize: 12, minWidth: 190 }}>
          <MenuItem value="on">Trail: selected car, 10 min</MenuItem>
          <MenuItem value="off">Trail: off</MenuItem>
        </Select>
        <FormControlLabel
          sx={{ ml: 0, '& .MuiFormControlLabel-label': { fontSize: 12 } }}
          control={<Checkbox size="small" sx={{ p: 0.4 }} checked={follow} onChange={(event) => setFollow(event.target.checked)} inputProps={{ 'aria-label': 'Follow the selected car' }} />}
          label="Follow the selected car"
        />
      </FilterBar>
      {map.error ? <ErrorNote error={map.error} /> : null}
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 1.5fr) minmax(320px, 1fr)' }, gap: 1, alignItems: 'start' }}>
        <Card sx={{ p: '6px' }} testId="map-card">
          {map.isLoading ? (
            <Loading label="Loading positions" />
          ) : (
            <VehicleMap
              vehicles={filtered}
              alerts={alerts}
              trail={showTrail ? trailPoints : []}
              selectedVehicleId={selectedVehicleId}
              follow={follow}
              showAlerts={showAlerts}
              height="calc(100vh - 200px)"
              centerRequest={centerRequest}
              onSelectVehicle={(id) => select(id)}
              onOpenAlert={(alert) => navigate(`/alerts/${alert.alertId}`)}
            />
          )}
        </Card>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1, minWidth: 0 }}>
          <Card title="Cars" note="GET /dashboard/map" testId="car-list">
            <DataTable columns={columns} rows={filtered} rowKey={(v) => v.vehicleId} selectedKey={selectedVehicleId} onRowClick={(v) => { select(v.vehicleId); setCenterRequest((n) => n + 1); }} empty="No cars match the filter" maxHeight={260} />
            <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
              click a row to select and center the car; a car without a sample is listed but not drawn
            </Typography>
          </Card>
          {selected ? (
            <Card
              title={
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  <span>
                    {selected.vehicleId}
                    {selected.label ? ` · ${selected.label}` : ''}
                  </span>
                  <StatusChip value={selected.connection ?? selected.status} />
                </Box>
              }
              testId="car-detail"
            >
              <Box sx={{ fontSize: 12, lineHeight: 1.8 }}>
                {selected.lat !== null && selected.lon !== null ? (
                  <>
                    {selected.lat.toFixed(4)}, {selected.lon.toFixed(4)}
                    {selected.headingDeg !== null ? ` · heading ${Math.round(selected.headingDeg)}°` : ''}
                    {selected.speedKph !== null ? ` · ${Math.round(selected.speedKph)} km/h` : ''}
                    <br />
                    {selected.driveMode ?? 'mode unknown'} {'·'} ignition {selected.ignition ? 'on' : 'off'} {'·'} sample {formatTime(selected.sampleAt)} ({ago(selected.sampleAt, now)} ago)
                  </>
                ) : (
                  <>no telemetry sample yet; the car is listed but not drawn</>
                )}
                <br />
                trail: {showTrail ? (trail.isLoading ? 'loading' : trail.error ? 'not available (C2)' : `${trailPoints.length} samples`) : 'off'}
                {' · '}open alerts: <b>{openByVehicle[selected.vehicleId] ?? 0}</b>
                {' · '}devices:{' '}
                {devices.data ? `${devices.data.filter((d) => d.status === 'ONLINE').length} online of ${devices.data.length}` : devices.error ? 'n/a (C2)' : devices.isLoading ? '...' : '-'}
              </Box>
              {showTrail && trail.error ? <Unavailable what="The trail (C2 telemetry)" error={trail.error} compact /> : null}
              <Box sx={{ mt: 1, display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
                <Button variant="contained" onClick={() => setCenterRequest((n) => n + 1)} disabled={selected.lat === null}>
                  Center
                </Button>
                <Button variant={follow ? 'contained' : 'outlined'} onClick={() => setFollow((f) => !f)} disabled={selected.lat === null}>
                  {follow ? 'Following' : 'Follow'}
                </Button>
                {permissions.includes('vehicles') && (
                  <Button variant="outlined" onClick={() => navigate(`/vehicles/${selected.vehicleId}`)}>
                    Open vehicle *
                  </Button>
                )}
                {permissions.includes('alerts') && (
                  <Button variant="outlined" onClick={() => navigate(`/alerts?vehicle=${selected.vehicleId}`)}>
                    Alerts of this car *
                  </Button>
                )}
                {permissions.includes('subscriptions') && (
                  <Button variant="outlined" onClick={() => navigate(`/subscriptions?vehicle=${selected.vehicleId}`)}>
                    Subscriptions
                  </Button>
                )}
              </Box>
            </Card>
          ) : (
            <Card title="Selected car">
              <Typography variant="caption">Click a marker or a row to see the detail and the trail of a car.</Typography>
            </Card>
          )}
        </Box>
      </Box>
    </Box>
  );
}
