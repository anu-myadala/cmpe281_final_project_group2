// OverviewPage: KPI tiles (7), vehicle map card, vehicle cards and recent alerts (8),
// the subscriptions panel of the selected vehicle (9). GET /dashboard/overview,
// GET /dashboard/map every 10 s, GET/PUT /subscriptions; live alert.created / updated / device.status.
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Typography from '@mui/material/Typography';
import Switch from '@mui/material/Switch';
import Checkbox from '@mui/material/Checkbox';
import FormControlLabel from '@mui/material/FormControlLabel';
import Alert from '@mui/material/Alert';
import { useAuth } from '../auth/AuthProvider';
import { useLiveEvents } from '../live/useLiveEvents';
import { queryCache, useOverview, useSubscriptions, useVehicleMap } from '../api/queries';
import { ApiClient, errorMessage, isApiError } from '../api/ApiClient';
import type { ServiceSubscription, SubscriptionBody, SubscriptionDto } from '../api/types';
import { Card, ErrorNote, KpiTile, Loading } from '../components/common';
import { VehicleMap } from '../components/VehicleMap';
import { VehicleCard } from '../components/VehicleCard';
import { RecentAlertsTable } from '../components/RecentAlertsTable';
import { useToast } from '../components/Toast';
import { formatTime } from '../util/format';
import { colors } from '../theme';

function SubscriptionsPanel({ subscription, plan, readOnly, tenantId }: { subscription: SubscriptionDto; plan: string; readOnly: boolean; tenantId: number | null }) {
  const { toast } = useToast();
  const [services, setServices] = useState<ServiceSubscription[]>(subscription.services);
  const [notifyRiders, setNotifyRiders] = useState(subscription.notifyRiders);
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    setServices(subscription.services);
    setNotifyRiders(subscription.notifyRiders);
    setDirty(false);
  }, [subscription]);

  const save = useMutation({
    mutationFn: (body: SubscriptionBody) => ApiClient.put<SubscriptionDto>(`/subscriptions/${subscription.vehicleId}`, body, { query: { tenantId } }),
    onSuccess: () => {
      toast('Settings saved');
      void queryCache.invalidateQueries({ queryKey: ['subscriptions'] });
    },
  });

  const order = ['EMERGENCY', 'ACCIDENT', 'SECURITY', 'SAFETY', 'PASSENGER', 'VEHICLE_HEALTH'];
  const ordered = [...services].sort((a, b) => order.indexOf(a.serviceCode) - order.indexOf(b.serviceCode));
  return (
    <Card title={`Subscriptions - ${subscription.vehicleId}`} sx={{ height: '100%' }} testId="subscriptions-panel">
      <Typography variant="caption">{plan} plan; the full editor is on the Subscriptions page</Typography>
      <Box sx={{ mt: 0.75, fontSize: 12, lineHeight: 2 }}>
        {ordered.map((service) => (
          <Box key={service.serviceCode} sx={{ display: 'flex', alignItems: 'center', gap: 0.5, color: service.available ? 'inherit' : '#888' }}>
            <Switch
              size="small"
              checked={service.enabled}
              disabled={readOnly || !service.available}
              onChange={(event) => {
                setServices((rows) => rows.map((row) => (row.serviceCode === service.serviceCode ? { ...row, enabled: event.target.checked } : row)));
                setDirty(true);
              }}
              inputProps={{ 'aria-label': `${service.serviceCode} enabled` }}
              sx={{ opacity: service.available ? 1 : 0.4 }}
            />
            <span>{service.serviceCode}</span>
            <Typography variant="caption">
              {service.available
                ? [service.channels.filter((c) => c !== 'DASHBOARD').join(', '), service.quietFrom && service.quietTo ? `quiet ${service.quietFrom}-${service.quietTo}` : '']
                    .filter(Boolean)
                    .join(', ')
                : '(design only)'}
            </Typography>
          </Box>
        ))}
        <FormControlLabel
          sx={{ ml: 0, '& .MuiFormControlLabel-label': { fontSize: 12 } }}
          control={
            <Checkbox
              size="small"
              sx={{ p: 0.4 }}
              checked={notifyRiders}
              disabled={readOnly}
              onChange={(event) => {
                setNotifyRiders(event.target.checked);
                setDirty(true);
              }}
            />
          }
          label="Notify riders during a trip"
        />
      </Box>
      {save.error ? <ErrorNote error={save.error} /> : null}
      {!readOnly && (
        <Box sx={{ mt: 1, display: 'flex', gap: 0.75 }}>
          <Button
            variant="contained"
            disabled={!dirty || save.isPending}
            onClick={() =>
              save.mutate({
                services: ordered.filter((s) => s.available).map((s) => ({ serviceCode: s.serviceCode, enabled: s.enabled, channels: s.channels, quietFrom: s.quietFrom, quietTo: s.quietTo })),
                notifyRiders,
              })
            }
          >
            Save settings
          </Button>
          <Button
            variant="outlined"
            disabled={!dirty}
            onClick={() => {
              setServices(subscription.services);
              setNotifyRiders(subscription.notifyRiders);
              setDirty(false);
            }}
          >
            Cancel
          </Button>
        </Box>
      )}
    </Card>
  );
}

export function OverviewPage() {
  const { tenantId, user, isStaff, permissions } = useAuth();
  const { polling, highlighted, liveAlerts } = useLiveEvents();
  const navigate = useNavigate();
  const overview = useOverview(tenantId, polling);
  const map = useVehicleMap(tenantId);
  const canSubscriptions = permissions.includes('subscriptions');
  const subscriptions = useSubscriptions(canSubscriptions ? tenantId : null);
  const [selectedVehicle, setSelectedVehicle] = useState<string | null>(null);

  const vehicles = overview.data?.vehicles ?? [];
  useEffect(() => {
    if (!selectedVehicle && vehicles.length > 0) setSelectedVehicle(vehicles[0].vehicleId);
  }, [vehicles, selectedVehicle]);

  // Alerts received live and not yet in the API answer go on top of the table.
  const recentAlerts = useMemo(() => {
    const rows = overview.data?.recentAlerts ?? [];
    const known = new Set(rows.map((r) => r.alertId));
    return [...liveAlerts.filter((a) => !known.has(a.alertId)), ...rows].slice(0, 10);
  }, [overview.data, liveAlerts]);

  const selectedSubscription = subscriptions.data?.find((s) => s.vehicleId === selectedVehicle) ?? subscriptions.data?.[0];
  const plan = overview.data?.tenant.plan ?? user?.plan ?? 'BASIC';

  if (tenantId === null && isStaff) {
    return <Alert severity="info">Select a tenant in the top bar to see its overview.</Alert>;
  }
  if (overview.isLoading) return <Loading label="Loading overview" />;
  if (overview.error) {
    const err = overview.error;
    return (
      <Alert severity="error" data-testid="overview-error">
        {isApiError(err) && err.status === 502 ? 'C2 or C3 did not answer and no cached overview exists (502). Start the stubs or enable the local fallback.' : errorMessage(err)}
      </Alert>
    );
  }
  const data = overview.data!;
  const kpis = data.kpis;
  const mapData = map.data;

  return (
    <Box>
      {data.degraded && (
        <Alert severity="warning" sx={{ mb: 1, py: 0, fontSize: 12 }} data-testid="degraded-note">
          C2 or C3 did not answer: showing cached or local data (degraded).
        </Alert>
      )}
      <Box sx={{ display: 'flex', gap: 1.25, mb: 1.25, flexWrap: 'wrap' }} data-testid="kpi-row">
        <KpiTile label="Vehicles" value={kpis.vehicles} testId="kpi-vehicles" />
        <KpiTile label="Online" value={kpis.online} hint="device.status" testId="kpi-online" />
        <KpiTile label="Open alerts" value={kpis.openAlerts} testId="kpi-open-alerts" />
        <KpiTile label="Alerts 24 h" value={kpis.alerts24h} testId="kpi-alerts-24h" />
        <KpiTile label="Events 24 h" value={kpis.events24h} testId="kpi-events-24h" />
      </Box>
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 1.9fr) minmax(300px, 1fr)' }, gap: 1.25, alignItems: 'start' }}>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25, minWidth: 0 }}>
          <Card
            title="Vehicle map"
            note={
              mapData
                ? `every 10 s · as of ${formatTime(mapData.asOf)} · ${mapData.vehicles.length} car${mapData.vehicles.length === 1 ? '' : 's'}, ${mapData.alerts.length} open alert${mapData.alerts.length === 1 ? '' : 's'}${mapData.degraded ? ' · pins degraded' : ''}`
                : 'loading positions'
            }
            actions={
              permissions.includes('map') ? (
                <Button variant="outlined" onClick={() => navigate(selectedVehicle ? `/map?vehicle=${selectedVehicle}` : '/map')} data-testid="open-full-map">
                  Open full map
                </Button>
              ) : null
            }
            testId="map-card"
          >
            <VehicleMap
              vehicles={mapData?.vehicles ?? []}
              alerts={mapData?.alerts ?? []}
              selectedVehicleId={selectedVehicle}
              height={240}
              showLegend={false}
              onSelectVehicle={setSelectedVehicle}
              onOpenAlert={(alert) => navigate(`/alerts/${alert.alertId}`)}
            />
            <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
              One marker per car at its newest telemetry sample, labeled with the car code; a pin is an open alert at the location of its event. Click a marker for the vehicle card, a pin for the alert.
            </Typography>
          </Card>
          <Card title={isStaff ? 'Vehicles' : 'My vehicles'} testId="vehicles-card">
            <Box sx={{ display: 'flex', gap: 1.25, flexWrap: 'wrap' }}>
              {vehicles.map((vehicle) => (
                <Box key={vehicle.vehicleId} onClick={() => setSelectedVehicle(vehicle.vehicleId)} sx={{ outline: vehicle.vehicleId === selectedVehicle ? `2px solid ${colors.primary}` : 'none', borderRadius: 1.5, cursor: 'pointer' }}>
                  <VehicleCard
                    vehicle={vehicle}
                    onOpen={() => navigate(`/vehicles/${vehicle.vehicleId}`)}
                    onMap={() => navigate(`/map?vehicle=${vehicle.vehicleId}`)}
                    onAlerts={permissions.includes('alerts') ? () => navigate(`/alerts?vehicle=${vehicle.vehicleId}`) : undefined}
                  />
                </Box>
              ))}
              {vehicles.length === 0 && <Typography variant="caption">No vehicles in this tenant.</Typography>}
              {!isStaff && (
                <Box sx={{ width: 230, border: '1.5px dashed #bbb', borderRadius: 1.5, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#777', fontSize: 12, p: 1.5, textAlign: 'center' }}>
                  + add a vehicle (C2{plan === 'BASIC' ? ', up to 3 on plan BASIC' : ''})
                </Box>
              )}
            </Box>
          </Card>
          <Card title={`Recent alerts${vehicles.length === 1 ? ` (${vehicles[0].vehicleId})` : ''}`} note="last 10, live via alert.created / alert.updated" testId="recent-alerts-card">
            <RecentAlertsTable rows={recentAlerts} highlighted={highlighted} tenantId={tenantId} showVehicle={vehicles.length !== 1} canAct={!isStaff || user?.role === 'SERVICE_STAFF' || user?.role === 'SYS_ADMIN'} />
          </Card>
        </Box>
        <Box sx={{ minWidth: 0 }}>
          {canSubscriptions && selectedSubscription ? (
            <SubscriptionsPanel subscription={selectedSubscription} plan={plan} readOnly={isStaff} tenantId={tenantId} />
          ) : canSubscriptions && subscriptions.isLoading ? (
            <Card title="Subscriptions">
              <Loading />
            </Card>
          ) : canSubscriptions ? (
            <Card title="Subscriptions">
              <Typography variant="caption">No vehicle to configure.</Typography>
            </Card>
          ) : null}
        </Box>
      </Box>
    </Box>
  );
}
