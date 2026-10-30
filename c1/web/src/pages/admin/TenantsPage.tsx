// TenantsPage: tenant table (8), edit panel (9), New tenant / Save / Suspend (10) with the
// confirm dialog for Suspend (12). GET, POST, PATCH /admin/tenants. IOT_ENGINEER and
// SERVICE_STAFF read the list only.
import { useEffect, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import TextField from '@mui/material/TextField';
import Select from '@mui/material/Select';
import MenuItem from '@mui/material/MenuItem';
import FormControl from '@mui/material/FormControl';
import InputLabel from '@mui/material/InputLabel';
import Typography from '@mui/material/Typography';
import Dialog from '@mui/material/Dialog';
import DialogTitle from '@mui/material/DialogTitle';
import DialogContent from '@mui/material/DialogContent';
import DialogActions from '@mui/material/DialogActions';
import { useAuth } from '../../auth/AuthProvider';
import { queryCache, useTenants } from '../../api/queries';
import { ApiClient, isApiError } from '../../api/ApiClient';
import type { Plan, TenantDto } from '../../api/types';
import { Card, ConfirmDialog, DataTable, ErrorNote, Loading, type Column } from '../../components/common';
import { StatusChip } from '../../components/StatusChip';
import { useToast } from '../../components/Toast';
import { formatDate } from '../../util/format';
import { AdminTabs } from './AdminTabs';

const PLANS: Array<{ value: Plan; label: string }> = [
  { value: 'BASIC', label: 'BASIC - 3 vehicles' },
  { value: 'FLEET', label: 'FLEET - 50 vehicles' },
  { value: 'ENTERPRISE', label: 'ENTERPRISE - 500 vehicles' },
];

function fieldErrors(error: unknown): Record<string, string> {
  if (isApiError(error) && error.field) return { [error.field]: error.message };
  return {};
}

export function TenantsPage() {
  const { user, setSelectedTenant } = useAuth();
  const navigate = useNavigate();
  const { toast } = useToast();
  const canWrite = user?.role === 'SYS_ADMIN';
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('');
  const tenants = useTenants({ q: q || undefined, status: status || undefined });
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [draft, setDraft] = useState<{ name: string; plan: Plan; timeZone: string } | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const [newDraft, setNewDraft] = useState({ name: '', tenantType: 'FLEET', plan: 'FLEET' as Plan, timeZone: 'America/Los_Angeles' });
  const [confirmSuspend, setConfirmSuspend] = useState(false);

  const rows = tenants.data?.items ?? [];
  const selected = rows.find((t) => t.tenantId === selectedId) ?? null;
  useEffect(() => {
    if (selected) setDraft({ name: selected.name, plan: selected.plan, timeZone: selected.timeZone });
    else setDraft(null);
  }, [selected]);

  const refresh = () => void queryCache.invalidateQueries({ queryKey: ['admin', 'tenants'] });

  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) => ApiClient.patch<TenantDto>(`/admin/tenants/${id}`, body),
    onSuccess: (data, { body }) => {
      toast(body.status === 'SUSPENDED' ? `Tenant ${data.name} suspended` : body.status === 'ACTIVE' ? `Tenant ${data.name} reactivated` : 'Tenant saved');
      refresh();
      setConfirmSuspend(false);
    },
  });
  const create = useMutation({
    mutationFn: (body: typeof newDraft) => ApiClient.post<TenantDto>('/admin/tenants', body),
    onSuccess: (data) => {
      toast(`Tenant ${data.name} created (${data.code})`);
      setNewOpen(false);
      setNewDraft({ name: '', tenantType: 'FLEET', plan: 'FLEET', timeZone: 'America/Los_Angeles' });
      refresh();
      setSelectedId(data.tenantId);
    },
  });

  const columns: Column<TenantDto>[] = [
    { key: 'name', header: 'Tenant', render: (t) => <b>{t.name}</b> },
    { key: 'code', header: 'Code' },
    { key: 'tenantType', header: 'Type' },
    { key: 'plan', header: 'Plan' },
    { key: 'maxVehicles', header: 'Max veh.', align: 'right' },
    { key: 'vehicles', header: 'Vehicles', align: 'right' },
    { key: 'users', header: 'Users', align: 'right' },
    { key: 'status', header: 'Status', render: (t) => <StatusChip value={t.status} /> },
    { key: 'createdAt', header: 'Created', render: (t) => formatDate(t.createdAt) },
  ];
  const patchErrors = fieldErrors(patch.error);
  const createErrors = fieldErrors(create.error);

  return (
    <Box>
      <AdminTabs />
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 1.7fr) minmax(300px, 1fr)' }, gap: 1.25, alignItems: 'start' }}>
        <Card title="Tenants" note="GET /admin/tenants?status&q&page&size" testId="tenants-card">
          <Box sx={{ display: 'flex', gap: 1, mb: 1, alignItems: 'center', flexWrap: 'wrap' }}>
            <TextField placeholder="Search name..." value={q} onChange={(event) => setQ(event.target.value)} sx={{ width: 180 }} inputProps={{ 'aria-label': 'Search tenants' }} />
            <Select value={status} onChange={(event) => setStatus(event.target.value)} displayEmpty sx={{ height: 32, fontSize: 12, minWidth: 120 }}>
              <MenuItem value="">Status: all</MenuItem>
              <MenuItem value="ACTIVE">ACTIVE</MenuItem>
              <MenuItem value="SUSPENDED">SUSPENDED</MenuItem>
            </Select>
            {canWrite && (
              <Button variant="contained" onClick={() => setNewOpen(true)} data-testid="new-tenant">
                New tenant
              </Button>
            )}
          </Box>
          {tenants.isLoading ? <Loading /> : tenants.error ? <ErrorNote error={tenants.error} /> : <DataTable columns={columns} rows={rows} rowKey={(t) => t.tenantId} selectedKey={selectedId} onRowClick={(t) => setSelectedId(t.tenantId)} testId="tenants-table" />}
          <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
            {tenants.data ? `${tenants.data.total} tenant${tenants.data.total === 1 ? '' : 's'}` : ''} {'·'} row click opens the edit panel {'·'} tenants are never deleted (BR-06)
          </Typography>
        </Card>
        <Card title={selected ? `Tenant: ${selected.name}` : 'Tenant'} testId="tenant-panel">
          {selected && draft ? (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25 }}>
              <TextField label="Name" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} disabled={!canWrite} error={Boolean(patchErrors.name)} helperText={patchErrors.name} />
              <TextField label="Type" value={selected.tenantType} disabled helperText="fixed at creation" />
              <FormControl disabled={!canWrite}>
                <InputLabel id="tenant-plan">Plan (max vehicles 3 / 50 / 500)</InputLabel>
                <Select labelId="tenant-plan" label="Plan (max vehicles 3 / 50 / 500)" value={draft.plan} onChange={(event) => setDraft({ ...draft, plan: event.target.value as Plan })}>
                  {PLANS.map((p) => (
                    <MenuItem key={p.value} value={p.value}>
                      {p.label}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
              <TextField label="Time zone" value={draft.timeZone} onChange={(event) => setDraft({ ...draft, timeZone: event.target.value })} disabled={!canWrite} error={Boolean(patchErrors.timeZone)} helperText={patchErrors.timeZone} />
              <Box sx={{ fontSize: 12 }}>
                Status <StatusChip value={selected.status} /> {'·'} code {selected.code} {'·'} {selected.vehicles} vehicles, {selected.users} users
              </Box>
              {patch.error && !Object.keys(patchErrors).length ? <ErrorNote error={patch.error} /> : null}
              <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
                {canWrite && (
                  <>
                    <Button variant="contained" disabled={patch.isPending} onClick={() => patch.mutate({ id: selected.tenantId, body: { name: draft.name, plan: draft.plan, timeZone: draft.timeZone } })}>
                      Save
                    </Button>
                    {selected.status === 'ACTIVE' ? (
                      <Button variant="outlined" color="error" sx={{ borderWidth: 1.5, fontWeight: 600 }} onClick={() => setConfirmSuspend(true)} data-testid="suspend-tenant">
                        Suspend
                      </Button>
                    ) : (
                      <Button variant="outlined" onClick={() => patch.mutate({ id: selected.tenantId, body: { status: 'ACTIVE' } })}>
                        Reactivate
                      </Button>
                    )}
                  </>
                )}
                <Button
                  variant="outlined"
                  onClick={() => {
                    setSelectedTenant(selected.tenantId);
                    navigate(user?.role === 'IOT_ENGINEER' ? '/vehicles' : '/overview');
                  }}
                >
                  Open as tenant
                </Button>
                {canWrite && (
                  <Button variant="outlined" onClick={() => navigate(`/admin/users?tenantId=${selected.tenantId}`)}>
                    Users
                  </Button>
                )}
              </Box>
              <Typography variant="caption">PATCH /admin/tenants/{'{id}'} {'{name?, plan?, status?, timeZone?}'} {'·'} audited as TENANT_UPDATE</Typography>
            </Box>
          ) : (
            <Typography variant="caption">Select a tenant in the table to edit it.</Typography>
          )}
        </Card>
      </Box>

      <ConfirmDialog
        open={confirmSuspend}
        title={`Suspend tenant ${selected?.name ?? ''}?`}
        confirmLabel="Suspend"
        busy={patch.isPending}
        onCancel={() => setConfirmSuspend(false)}
        onConfirm={() => selected && patch.mutate({ id: selected.tenantId, body: { status: 'SUSPENDED' } })}
      >
        Users of this tenant will get 403 at login until it is reactivated. Vehicles, alerts and settings are kept (BR-06).
        <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
          PATCH /admin/tenants/{'{id}'} {'{status: SUSPENDED}'} - audited as TENANT_UPDATE with the before and after values (BR-12)
        </Typography>
      </ConfirmDialog>

      <Dialog open={newOpen} onClose={() => setNewOpen(false)} maxWidth="xs" fullWidth>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate(newDraft);
          }}
        >
          <DialogTitle>New tenant</DialogTitle>
          <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.25, pt: '8px !important' }}>
            <TextField label="Name" value={newDraft.name} onChange={(event) => setNewDraft({ ...newDraft, name: event.target.value })} autoFocus required error={Boolean(createErrors.name)} helperText={createErrors.name} inputProps={{ 'data-testid': 'tenant-name' }} />
            <FormControl>
              <InputLabel id="new-type">Type</InputLabel>
              <Select labelId="new-type" label="Type" value={newDraft.tenantType} onChange={(event) => setNewDraft({ ...newDraft, tenantType: event.target.value })}>
                <MenuItem value="OWNER">OWNER (household)</MenuItem>
                <MenuItem value="FLEET">FLEET</MenuItem>
                <MenuItem value="SERVICE_PROVIDER">SERVICE_PROVIDER</MenuItem>
              </Select>
            </FormControl>
            <FormControl>
              <InputLabel id="new-plan">Plan</InputLabel>
              <Select labelId="new-plan" label="Plan" value={newDraft.plan} onChange={(event) => setNewDraft({ ...newDraft, plan: event.target.value as Plan })}>
                {PLANS.map((p) => (
                  <MenuItem key={p.value} value={p.value}>
                    {p.label}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <TextField label="Time zone" value={newDraft.timeZone} onChange={(event) => setNewDraft({ ...newDraft, timeZone: event.target.value })} />
            {create.error && !Object.keys(createErrors).length ? <ErrorNote error={create.error} /> : null}
          </DialogContent>
          <DialogActions sx={{ px: 2, pb: 1.5 }}>
            <Button variant="outlined" onClick={() => setNewOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="contained" disabled={create.isPending || newDraft.name.trim().length < 2} data-testid="tenant-create">
              Create
            </Button>
          </DialogActions>
        </form>
      </Dialog>
    </Box>
  );
}
