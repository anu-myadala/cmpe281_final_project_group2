// SubscriptionsPage: one SubscriptionCard per vehicle (8) with Save / Cancel (10).
// GET /subscriptions, GET /intelligence-services, PUT /subscriptions/{vehicleId}; 422 BR-04 inline.
import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import Box from '@mui/material/Box';
import Alert from '@mui/material/Alert';
import Typography from '@mui/material/Typography';
import { useAuth } from '../auth/AuthProvider';
import { useLiveEvents } from '../live/useLiveEvents';
import { queryCache, useOverview, useSubscriptions } from '../api/queries';
import { ApiClient } from '../api/ApiClient';
import type { SubscriptionBody, SubscriptionDto } from '../api/types';
import { SubscriptionCard } from '../components/SubscriptionCard';
import { ErrorNote, Loading } from '../components/common';
import { useToast } from '../components/Toast';

export function SubscriptionsPage() {
  const { tenantId, user, isStaff } = useAuth();
  const { polling } = useLiveEvents();
  const { toast } = useToast();
  const [params] = useSearchParams();
  const subscriptions = useSubscriptions(tenantId);
  const overview = useOverview(tenantId, polling);
  const [errors, setErrors] = useState<Record<string, unknown>>({});
  const [savingId, setSavingId] = useState<string | null>(null);
  const focusRef = useRef<HTMLDivElement>(null);

  const save = useMutation({
    mutationFn: ({ vehicleId, body }: { vehicleId: string; body: SubscriptionBody }) =>
      ApiClient.put<SubscriptionDto>(`/subscriptions/${vehicleId}`, body, { query: { tenantId } }),
    onMutate: ({ vehicleId }) => {
      setSavingId(vehicleId);
      setErrors((e) => ({ ...e, [vehicleId]: null }));
    },
    onSuccess: (data) => {
      toast('Settings saved');
      queryCache.setQueryData<SubscriptionDto[]>([...['subscriptions', tenantId], 'all'], (rows) => (rows ? rows.map((row) => (row.vehicleId === data.vehicleId ? data : row)) : rows));
      void queryCache.invalidateQueries({ queryKey: ['subscriptions'] });
    },
    onError: (error, { vehicleId }) => setErrors((e) => ({ ...e, [vehicleId]: error })),
    onSettled: () => setSavingId(null),
  });

  const wanted = params.get('vehicle');
  useEffect(() => {
    if (wanted && focusRef.current) focusRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [wanted, subscriptions.data]);

  if (tenantId === null && isStaff) return <Alert severity="info">Select a tenant in the top bar to see its subscriptions.</Alert>;
  if (subscriptions.isLoading) return <Loading label="Loading subscriptions" />;
  if (subscriptions.error) return <ErrorNote error={subscriptions.error} />;

  const plan = overview.data?.tenant.plan ?? user?.plan ?? 'BASIC';
  const connections = new Map((overview.data?.vehicles ?? []).map((v) => [v.vehicleId, v]));
  const rows = subscriptions.data ?? [];

  return (
    <Box>
      {rows.length === 0 && <Alert severity="info">No vehicles in this tenant yet.</Alert>}
      {rows.map((subscription) => {
        const vehicle = connections.get(subscription.vehicleId);
        return (
          <div key={subscription.vehicleId} ref={subscription.vehicleId === wanted ? focusRef : undefined}>
            <SubscriptionCard
              subscription={subscription}
              plan={plan}
              connection={vehicle?.connection === 'NEVER' ? vehicle.status : vehicle?.connection}
              vehicleNote={vehicle?.vin ? `VIN ${vehicle.vin}` : undefined}
              readOnly={isStaff}
              saving={savingId === subscription.vehicleId}
              saveError={errors[subscription.vehicleId]}
              onSave={(body) => save.mutate({ vehicleId: subscription.vehicleId, body })}
              onReset={() => setErrors((e) => ({ ...e, [subscription.vehicleId]: null }))}
            />
          </div>
        );
      })}
      <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
        DASHBOARD is always on. Quiet hours need both times and they must differ (BR-04); C3 reads the saved row when it creates the next alert (BR-05).
      </Typography>
    </Box>
  );
}
