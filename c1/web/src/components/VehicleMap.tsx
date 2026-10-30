// VehicleMap: one Leaflet map (OpenStreetMap tiles) with one round marker per car labeled
// with the car code (green ONLINE, amber OFFLINE, gray no sample; heading arrow while the
// car moves), one pin per open alert in its severity color, and the trail polyline of the
// selected car. The view keeps its zoom and center between refreshes unless follow is on.
import { useEffect, useMemo, useRef } from 'react';
import { MapContainer, Marker, Polyline, TileLayer, ZoomControl, useMap, CircleMarker, Tooltip as LeafletTooltip } from 'react-leaflet';
import L, { type LatLngBoundsExpression, type LatLngExpression } from 'leaflet';
import 'leaflet/dist/leaflet.css';
import Box from '@mui/material/Box';
import type { MapAlert, MapVehicle } from '../api/types';
import { CONNECTION_COLORS, SEVERITY_COLORS, colors } from '../theme';

export const SJSU: LatLngExpression = [37.3352, -121.8811];

export interface VehicleMapProps {
  vehicles: MapVehicle[];
  alerts: MapAlert[];
  trail?: Array<[number, number]>;
  selectedVehicleId?: string | null;
  follow?: boolean;
  showAlerts?: boolean;
  showLegend?: boolean;
  height?: number | string;
  onSelectVehicle?: (vehicleId: string) => void;
  onOpenAlert?: (alert: MapAlert) => void;
  /** Bumps when the caller wants the view centered on the selected car once. */
  centerRequest?: number;
}

function connectionColor(vehicle: MapVehicle): string {
  if (vehicle.connection === 'ONLINE') return CONNECTION_COLORS.ONLINE;
  if (vehicle.connection === 'OFFLINE') return CONNECTION_COLORS.OFFLINE;
  return CONNECTION_COLORS.NONE;
}

function carIcon(vehicle: MapVehicle, selected: boolean): L.DivIcon {
  const color = connectionColor(vehicle);
  const code = vehicle.vehicleId;
  const short = code.replace(/^AV-/, '');
  const moving = (vehicle.speedKph ?? 0) > 0 && vehicle.headingDeg !== null;
  const arrow = moving
    ? `<path transform="translate(24 24) rotate(${vehicle.headingDeg})" d="M0,-22 L6,-13 L-6,-13 z" fill="${color}" stroke="#fff" stroke-width="1"/>`
    : '';
  const ring = selected ? `<circle cx="24" cy="24" r="18" fill="none" stroke="${colors.primary}" stroke-width="2.5"/>` : '';
  const html = `<svg xmlns="http://www.w3.org/2000/svg" width="110" height="48" viewBox="0 0 110 48" style="overflow:visible">
      ${ring}${arrow}
      <circle cx="24" cy="24" r="11" fill="${color}" stroke="#fff" stroke-width="2.5"/>
      <text x="24" y="27.5" font-size="8" font-weight="700" fill="#fff" text-anchor="middle" font-family="Inter,Helvetica,Arial">${short}</text>
      <text x="40" y="28" font-size="10.5" font-weight="600" fill="#1f2328" stroke="#fff" stroke-width="3" paint-order="stroke" font-family="Inter,Helvetica,Arial">${code}</text>
    </svg>`;
  return L.divIcon({ html, className: 'scicp-car-marker', iconSize: [110, 48], iconAnchor: [24, 24] });
}

function pinIcon(alert: MapAlert): L.DivIcon {
  const color = SEVERITY_COLORS[alert.severity] ?? SEVERITY_COLORS.LOW;
  const label = `${alert.type} ${alert.alertId}`;
  const html = `<svg xmlns="http://www.w3.org/2000/svg" width="140" height="40" viewBox="0 0 140 40" style="overflow:visible">
      <path transform="translate(14 38)" d="M0,0 C-7,-9 -11,-14 -11,-21 a11,11 0 1,1 22,0 c0,7 -4,12 -11,21 z" fill="${color}" stroke="#fff" stroke-width="1.5"/>
      <circle cx="14" cy="17" r="4" fill="#fff"/>
      <text x="30" y="21" font-size="10" font-weight="600" fill="${color}" stroke="#fff" stroke-width="3" paint-order="stroke" font-family="Inter,Helvetica,Arial">${label}</text>
    </svg>`;
  return L.divIcon({ html, className: 'scicp-alert-pin', iconSize: [140, 40], iconAnchor: [14, 38] });
}

function ViewKeeper({
  positions,
  selected,
  follow,
  centerRequest,
}: {
  positions: Array<[number, number]>;
  selected: [number, number] | null;
  follow: boolean;
  centerRequest?: number;
}) {
  const map = useMap();
  const fitted = useRef(false);
  const lastCenterRequest = useRef(centerRequest);

  // Fit the markers once, on the first data; afterwards the view is the user's.
  useEffect(() => {
    if (fitted.current || positions.length === 0) return;
    fitted.current = true;
    if (positions.length === 1) map.setView(positions[0], 15);
    else map.fitBounds(positions as LatLngBoundsExpression, { padding: [40, 40], maxZoom: 16 });
  }, [positions, map]);

  useEffect(() => {
    if (follow && selected) map.panTo(selected, { animate: true });
  }, [follow, selected, map]);

  useEffect(() => {
    if (centerRequest !== lastCenterRequest.current) {
      lastCenterRequest.current = centerRequest;
      if (selected) map.setView(selected, Math.max(map.getZoom(), 15), { animate: true });
    }
  }, [centerRequest, selected, map]);

  useEffect(() => {
    // The container can be sized after mount (cards on a grid): re-measure.
    const timer = window.setTimeout(() => map.invalidateSize(), 50);
    return () => window.clearTimeout(timer);
  }, [map]);
  return null;
}

export function VehicleMap({
  vehicles,
  alerts,
  trail = [],
  selectedVehicleId = null,
  follow = false,
  showAlerts = true,
  showLegend = true,
  height = 400,
  onSelectVehicle,
  onOpenAlert,
  centerRequest,
}: VehicleMapProps) {
  const placed = useMemo(() => vehicles.filter((v) => v.lat !== null && v.lon !== null), [vehicles]);
  const positions = useMemo<Array<[number, number]>>(() => {
    const points: Array<[number, number]> = placed.map((v) => [v.lat as number, v.lon as number]);
    if (showAlerts) alerts.forEach((a) => points.push([a.lat, a.lon]));
    return points;
  }, [placed, alerts, showAlerts]);
  const selectedVehicle = placed.find((v) => v.vehicleId === selectedVehicleId) ?? null;
  const selectedPos: [number, number] | null = selectedVehicle ? [selectedVehicle.lat as number, selectedVehicle.lon as number] : null;

  return (
    <Box sx={{ position: 'relative', height, borderRadius: 1, overflow: 'hidden', bgcolor: '#f3f0e9', '& .leaflet-container': { height: '100%', width: '100%', background: '#f3f0e9', fontFamily: 'inherit' }, '& .leaflet-div-icon': { background: 'transparent', border: 'none' } }} data-testid="vehicle-map">
      <MapContainer center={SJSU} zoom={15} scrollWheelZoom zoomControl={false} style={{ height: '100%', width: '100%' }} attributionControl>
        <ZoomControl position="topright" />
        <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" />
        <ViewKeeper positions={positions} selected={selectedPos} follow={follow} centerRequest={centerRequest} />
        {trail.length > 1 && (
          <>
            <Polyline positions={trail} pathOptions={{ color: colors.primary, weight: 3, opacity: 0.55, lineJoin: 'round' }} />
            {trail.slice(0, -1).map((point, index) => (
              <CircleMarker key={`t${index}`} center={point} radius={2.5} pathOptions={{ color: colors.primary, fillColor: colors.primary, fillOpacity: 0.8, stroke: false }} />
            ))}
          </>
        )}
        {showAlerts &&
          alerts.map((alert) => (
            <Marker
              key={`a${alert.alertId}`}
              position={[alert.lat, alert.lon]}
              icon={pinIcon(alert)}
              zIndexOffset={500}
              eventHandlers={{ click: () => onOpenAlert?.(alert) }}
              alt={`Alert ${alert.alertId}`}
            >
              <LeafletTooltip direction="top" offset={[0, -36]}>
                {alert.severity} {alert.type} on {alert.vehicleId} - open the alert
              </LeafletTooltip>
            </Marker>
          ))}
        {placed.map((vehicle) => (
          <Marker
            key={vehicle.vehicleId}
            position={[vehicle.lat as number, vehicle.lon as number]}
            icon={carIcon(vehicle, vehicle.vehicleId === selectedVehicleId)}
            zIndexOffset={vehicle.vehicleId === selectedVehicleId ? 1000 : 100}
            eventHandlers={{ click: () => onSelectVehicle?.(vehicle.vehicleId) }}
            alt={`Vehicle ${vehicle.vehicleId}`}
          >
            <LeafletTooltip direction="top" offset={[0, -14]}>
              {vehicle.vehicleId}
              {vehicle.label ? ` · ${vehicle.label}` : ''}
              {` · ${vehicle.connection ?? 'no sample'}`}
              {vehicle.speedKph !== null ? ` · ${Math.round(vehicle.speedKph)} km/h` : ''}
            </LeafletTooltip>
          </Marker>
        ))}
      </MapContainer>
      {showLegend && (
        <Box sx={{ position: 'absolute', left: 10, top: 10, zIndex: 800, bgcolor: 'rgba(255,255,255,.92)', border: '1px solid #c9c7c0', borderRadius: 1, px: 1, py: 0.5, fontSize: 9.5, lineHeight: 1.6, color: '#333' }}>
          <Legend color={CONNECTION_COLORS.ONLINE} text="car ONLINE (newest sample)" />
          <Legend color={CONNECTION_COLORS.OFFLINE} text="car OFFLINE (sample > 30 s)" />
          <Legend color={CONNECTION_COLORS.NONE} text="car without a sample" />
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
            <svg width="12" height="12" viewBox="-12 -24 24 26">
              <path d="M0,0 C-7,-9 -11,-14 -11,-21 a11,11 0 1,1 22,0 c0,7 -4,12 -11,21 z" fill={SEVERITY_COLORS.CRITICAL} />
            </svg>
            open alert (severity color)
          </Box>
          {trail.length > 1 && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
              <Box sx={{ width: 12, height: 3, bgcolor: colors.primary, opacity: 0.55, borderRadius: 1 }} /> trail of the selected car (10 min)
            </Box>
          )}
        </Box>
      )}
      {placed.length === 0 && (
        <Box sx={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 700, pointerEvents: 'none' }}>
          <Box sx={{ bgcolor: 'rgba(255,255,255,.9)', border: '1px dashed #c9c7c0', borderRadius: 1, px: 1.5, py: 0.75, fontSize: 12, color: '#555' }}>
            No car has sent a position yet
          </Box>
        </Box>
      )}
    </Box>
  );
}

function Legend({ color, text }: { color: string; text: string }) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
      <Box sx={{ width: 12, height: 12, borderRadius: '50%', bgcolor: color }} /> {text}
    </Box>
  );
}

/** Converts C2 telemetry samples (newest first) to a polyline, oldest first. */
export function trailFromSamples(samples: Array<{ location?: { coordinates: [number, number] } | null; lat?: number | null; lon?: number | null }>): Array<[number, number]> {
  const points: Array<[number, number]> = [];
  for (const sample of samples) {
    if (sample.location?.coordinates) points.push([sample.location.coordinates[1], sample.location.coordinates[0]]);
    else if (typeof sample.lat === 'number' && typeof sample.lon === 'number') points.push([sample.lat, sample.lon]);
  }
  return points.reverse();
}
