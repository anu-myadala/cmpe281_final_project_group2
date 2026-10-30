// SubscriptionCard: one card per vehicle with a ServiceToggleRow per intelligence service
// (enabled switch, channel checkboxes allowed by the plan, QuietHoursPicker), the notify
// riders flag, Save / Cancel, and the inline field errors of a 422 BR-04 answer.
import { useEffect, useMemo, useState } from 'react';
import Box from '@mui/material/Box';
import Switch from '@mui/material/Switch';
import Checkbox from '@mui/material/Checkbox';
import FormControlLabel from '@mui/material/FormControlLabel';
import Button from '@mui/material/Button';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import Tooltip from '@mui/material/Tooltip';
import type { Channel, Plan, ServiceSubscription, SubscriptionBody, SubscriptionDto } from '../api/types';
import { ApiError } from '../api/ApiClient';
import { Card } from './common';
import { StatusChip } from './StatusChip';
import { colors } from '../theme';

export const PLAN_CHANNELS: Record<Plan, Channel[]> = {
  BASIC: ['DASHBOARD', 'EMAIL'],
  FLEET: ['DASHBOARD', 'EMAIL', 'SMS', 'PUSH'],
  ENTERPRISE: ['DASHBOARD', 'EMAIL', 'SMS', 'PUSH'],
};
const ALL_CHANNELS: Channel[] = ['DASHBOARD', 'EMAIL', 'SMS', 'PUSH'];
const SERVICE_ORDER = ['EMERGENCY', 'ACCIDENT', 'SECURITY', 'SAFETY', 'PASSENGER', 'VEHICLE_HEALTH'];

export function QuietHoursPicker({
  from,
  to,
  disabled,
  error,
  onChange,
}: {
  from: string | null;
  to: string | null;
  disabled?: boolean;
  error?: string;
  onChange: (from: string | null, to: string | null) => void;
}) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
      <TextField
        type="time"
        value={from ?? ''}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value || null, to)}
        inputProps={{ step: 60, 'aria-label': 'Quiet from', style: { fontSize: 12, padding: '3px 6px', width: 72 } }}
        error={Boolean(error)}
      />
      <Typography variant="caption">-</Typography>
      <TextField
        type="time"
        value={to ?? ''}
        disabled={disabled}
        onChange={(event) => onChange(from, event.target.value || null)}
        inputProps={{ step: 60, 'aria-label': 'Quiet to', style: { fontSize: 12, padding: '3px 6px', width: 72 } }}
        error={Boolean(error)}
      />
      {(from || to) && !disabled && (
        <Button size="small" variant="text" sx={{ fontSize: 11, minWidth: 0, px: 0.5 }} onClick={() => onChange(null, null)}>
          clear
        </Button>
      )}
    </Box>
  );
}

export function ServiceToggleRow({
  service,
  allowedChannels,
  readOnly,
  errors,
  onChange,
}: {
  service: ServiceSubscription;
  allowedChannels: Channel[];
  readOnly: boolean;
  errors: Record<string, string>;
  onChange: (next: ServiceSubscription) => void;
}) {
  const dim = !service.available;
  const disabled = readOnly || dim;
  const toggleChannel = (channel: Channel, on: boolean) => {
    const set = new Set(service.channels);
    if (on) set.add(channel);
    else set.delete(channel);
    set.add('DASHBOARD');
    onChange({ ...service, channels: ALL_CHANNELS.filter((c) => set.has(c)) });
  };
  const rowError = errors[`${service.serviceCode}.enabled`] ?? errors[`${service.serviceCode}.serviceCode`];
  const channelError = errors[`${service.serviceCode}.channels`];
  const quietError = errors[`${service.serviceCode}.quietHours`] ?? errors[`${service.serviceCode}.quietFrom`] ?? errors[`${service.serviceCode}.quietTo`];
  return (
    <Box
      sx={{
        display: 'grid',
        gridTemplateColumns: { xs: '1fr', md: '170px 60px minmax(260px, 1fr) 220px' },
        alignItems: 'center',
        gap: 1,
        py: 0.6,
        borderTop: '1px solid #e3e0d8',
        color: dim ? '#888' : 'inherit',
        fontSize: 12,
      }}
      data-testid={`service-row-${service.serviceCode}`}
    >
      <Box sx={{ display: 'flex', alignItems: 'center' }}>
        <Switch
          size="small"
          checked={service.enabled}
          disabled={disabled}
          onChange={(event) => onChange({ ...service, enabled: event.target.checked })}
          inputProps={{ 'aria-label': `${service.serviceCode} enabled` }}
          sx={{ opacity: dim ? 0.4 : 1 }}
        />
        <Box>
          <Box sx={{ fontWeight: 600 }}>{service.serviceCode}</Box>
          <Typography variant="caption">{service.name}</Typography>
        </Box>
      </Box>
      <Box>{dim ? <Typography variant="caption">design only</Typography> : service.enabled ? 'on' : 'off'}</Box>
      <Box>
        {dim ? (
          <Typography variant="caption">available = false, cannot be enabled</Typography>
        ) : (
          <Box sx={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center' }}>
            {ALL_CHANNELS.map((channel) => {
              const allowed = allowedChannels.includes(channel);
              const always = channel === 'DASHBOARD';
              const control = (
                <FormControlLabel
                  key={channel}
                  sx={{ mr: 1.5, '& .MuiFormControlLabel-label': { fontSize: 12, color: allowed ? 'inherit' : '#999' } }}
                  control={
                    <Checkbox
                      size="small"
                      sx={{ p: 0.4 }}
                      checked={always || service.channels.includes(channel)}
                      disabled={readOnly || always || !allowed}
                      onChange={(event) => toggleChannel(channel, event.target.checked)}
                      inputProps={{ 'aria-label': `${service.serviceCode} ${channel}` }}
                    />
                  }
                  label={
                    <>
                      {channel}
                      {always && <Typography variant="caption"> (always)</Typography>}
                    </>
                  }
                />
              );
              return allowed ? control : (
                <Tooltip key={channel} title="Not in your plan">
                  <span>{control}</span>
                </Tooltip>
              );
            })}
            {channelError && (
              <Typography sx={{ color: colors.destructive, fontSize: 11.5, ml: 0.5 }} data-testid="field-error">
                {channelError}
              </Typography>
            )}
          </Box>
        )}
      </Box>
      <Box>
        {!dim && (
          <>
            <QuietHoursPicker from={service.quietFrom} to={service.quietTo} disabled={readOnly} error={quietError} onChange={(from, to) => onChange({ ...service, quietFrom: from, quietTo: to })} />
            {quietError && (
              <Typography sx={{ color: colors.destructive, fontSize: 11.5 }} data-testid="field-error">
                {quietError}
              </Typography>
            )}
          </>
        )}
        {rowError && (
          <Typography sx={{ color: colors.destructive, fontSize: 11.5 }} data-testid="field-error">
            {rowError}
          </Typography>
        )}
      </Box>
    </Box>
  );
}

export function errorsFromApi(error: unknown): { fields: Record<string, string>; general: string | null } {
  if (error instanceof ApiError) {
    const field = error.field;
    if (field) {
      const allowed = Array.isArray(error.details.allowed) ? ` (allowed: ${(error.details.allowed as string[]).join(', ')})` : '';
      return { fields: { [field]: `${error.message}${allowed}` }, general: null };
    }
    return { fields: {}, general: error.message };
  }
  return { fields: {}, general: error instanceof Error ? error.message : 'Save failed' };
}

export function SubscriptionCard({
  subscription,
  plan,
  connection,
  vehicleNote,
  readOnly,
  saving,
  saveError,
  onSave,
  onReset,
}: {
  subscription: SubscriptionDto;
  plan: Plan;
  connection?: string | null;
  vehicleNote?: string;
  readOnly: boolean;
  saving: boolean;
  saveError: unknown;
  onSave: (body: SubscriptionBody) => void;
  onReset?: () => void;
}) {
  const [services, setServices] = useState<ServiceSubscription[]>(subscription.services);
  const [notifyRiders, setNotifyRiders] = useState(subscription.notifyRiders);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setServices(subscription.services);
    setNotifyRiders(subscription.notifyRiders);
    setDirty(false);
  }, [subscription]);

  const ordered = useMemo(
    () => [...services].sort((a, b) => SERVICE_ORDER.indexOf(a.serviceCode) - SERVICE_ORDER.indexOf(b.serviceCode)),
    [services],
  );
  const { fields, general } = useMemo(() => (saveError ? errorsFromApi(saveError) : { fields: {}, general: null }), [saveError]);
  const allowed = PLAN_CHANNELS[plan] ?? PLAN_CHANNELS.BASIC;

  const update = (next: ServiceSubscription) => {
    setServices((rows) => rows.map((row) => (row.serviceCode === next.serviceCode ? next : row)));
    setDirty(true);
  };

  const save = () => {
    onSave({
      services: ordered
        .filter((s) => s.available)
        .map((s) => ({ serviceCode: s.serviceCode, enabled: s.enabled, channels: s.channels, quietFrom: s.quietFrom, quietTo: s.quietTo })),
      notifyRiders,
    });
  };

  const cancel = () => {
    setServices(subscription.services);
    setNotifyRiders(subscription.notifyRiders);
    setDirty(false);
    onReset?.();
  };

  return (
    <Card
      testId={`subscription-card-${subscription.vehicleId}`}
      title={
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <span>{subscription.vehicleId}</span>
          {connection && <StatusChip value={connection} />}
        </Box>
      }
      note={`${subscription.label ?? ''}${vehicleNote ? ` · ${vehicleNote}` : ''} · plan ${plan}: channels ${allowed.join(', ')}`}
      sx={{ mb: 1.25 }}
    >
      <Box sx={{ display: { xs: 'none', md: 'grid' }, gridTemplateColumns: '170px 60px minmax(260px, 1fr) 220px', gap: 1, fontWeight: 700, fontSize: 12, bgcolor: '#f5f4f0', px: 0.5, py: 0.5 }}>
        <span>Service</span>
        <span>Enabled</span>
        <span>Channels</span>
        <span>Quiet hours (from - to)</span>
      </Box>
      {ordered.map((service) => (
        <ServiceToggleRow key={service.serviceCode} service={service} allowedChannels={allowed} readOnly={readOnly} errors={fields} onChange={update} />
      ))}
      <Box sx={{ mt: 1, fontSize: 12, display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 1 }}>
        <FormControlLabel
          sx={{ '& .MuiFormControlLabel-label': { fontSize: 12 } }}
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
              inputProps={{ 'aria-label': 'Notify riders during an active trip' }}
            />
          }
          label="Notify riders during an active trip"
        />
        <Typography variant="caption">riders get email for whichever services are enabled</Typography>
      </Box>
      {general && (
        <Typography sx={{ color: colors.destructive, fontSize: 12, mt: 0.5 }} data-testid="save-error">
          {general}
        </Typography>
      )}
      {!readOnly && (
        <Box sx={{ mt: 1.25, display: 'flex', gap: 0.75, alignItems: 'center' }}>
          <Button variant="contained" onClick={save} disabled={saving || !dirty} data-testid="subscription-save">
            Save
          </Button>
          <Button variant="outlined" onClick={cancel} disabled={saving || !dirty}>
            Cancel
          </Button>
          {subscription.updatedAt && (
            <Typography variant="caption" sx={{ ml: 1 }}>
              last saved {new Date(subscription.updatedAt).toLocaleString()}
            </Typography>
          )}
        </Box>
      )}
      {readOnly && <Typography variant="caption">Staff see the subscriptions read-only (BR-01).</Typography>}
    </Card>
  );
}
