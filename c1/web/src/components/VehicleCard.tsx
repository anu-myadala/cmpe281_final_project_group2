// VehicleCard: one vehicle of the overview with its connection, last event, open alerts.
import Box from '@mui/material/Box';
import Paper from '@mui/material/Paper';
import Button from '@mui/material/Button';
import Typography from '@mui/material/Typography';
import type { VehicleCardDto } from '../api/types';
import { StatusChip } from './StatusChip';
import { ago } from '../util/format';
import { colors } from '../theme';

export function VehicleCard({
  vehicle,
  onOpen,
  onMap,
  onAlerts,
}: {
  vehicle: VehicleCardDto;
  onOpen?: () => void;
  onMap?: () => void;
  onAlerts?: () => void;
}) {
  const conn = vehicle.connection === 'NEVER' || !vehicle.connection ? vehicle.status : vehicle.connection;
  const summary = vehicle.deviceSummary;
  return (
    <Paper sx={{ p: '8px 10px', width: 230, fontSize: 12 }} data-testid="vehicle-card" data-vehicle={vehicle.vehicleId}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Typography sx={{ fontSize: 14, fontWeight: 700 }}>{vehicle.vehicleId}</Typography>
        <StatusChip value={conn} />
      </Box>
      <Typography variant="caption" sx={{ display: 'block', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {vehicle.vin ? `VIN ${vehicle.vin}` : ''}
        {vehicle.label ? `${vehicle.vin ? ' · ' : ''}${vehicle.label}` : ''}
      </Typography>
      <Box sx={{ mt: 0.5, lineHeight: 1.6 }}>
        last event: {vehicle.lastEventAt ? `${ago(vehicle.lastEventAt)} ago` : 'none'}
        <br />
        open alerts: <b>{vehicle.openAlerts ?? 0}</b>
        {vehicle.topClass24h ? (
          <>
            {' · '}top class 24 h: <b>{vehicle.topClass24h}</b>
          </>
        ) : null}
        {summary ? (
          <>
            <br />
            devices: {summary.online} online
            {summary.offline ? `, ${summary.offline} offline` : ''}
            {summary.fault ? `, ${summary.fault} fault` : ''}
          </>
        ) : null}
      </Box>
      <Box sx={{ mt: 0.75, display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
        <Button variant="outlined" onClick={onOpen}>
          Open vehicle
        </Button>
        <Button variant="outlined" onClick={onMap}>
          On map
        </Button>
        {onAlerts && (
          <Button variant="outlined" onClick={onAlerts} sx={{ color: colors.primaryDark }}>
            Alerts
          </Button>
        )}
      </Box>
    </Paper>
  );
}
