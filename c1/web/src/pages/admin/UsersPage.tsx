// UsersPage: user table (8), edit panel (9), Invite user / Save / Reset password / Disable (10)
// with confirm dialogs (12). GET, POST, PATCH /admin/users, POST /admin/users/{id}/reset-password.
import { useEffect, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
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
import { queryCache, useTenants, useUsers } from '../../api/queries';
import { ApiClient, isApiError } from '../../api/ApiClient';
import type { AdminUserDto, Role } from '../../api/types';
import { Card, ConfirmDialog, DataTable, ErrorNote, Loading, type Column } from '../../components/common';
import { StatusChip } from '../../components/StatusChip';
import { useToast } from '../../components/Toast';
import { formatDateTime } from '../../util/format';
import { AdminTabs } from './AdminTabs';

const ROLES: Role[] = ['OWNER', 'FLEET_MANAGER', 'RIDER', 'IOT_ENGINEER', 'SERVICE_STAFF', 'SYS_ADMIN'];
const STAFF: Role[] = ['IOT_ENGINEER', 'SERVICE_STAFF', 'SYS_ADMIN'];

function fieldErrors(error: unknown): Record<string, string> {
  if (isApiError(error) && error.field) return { [error.field]: error.message };
  return {};
}

export function UsersPage() {
  const { toast } = useToast();
  const [params] = useSearchParams();
  const [tenantFilter, setTenantFilter] = useState<string>(params.get('tenantId') ?? '');
  const [roleFilter, setRoleFilter] = useState('');
  const [q, setQ] = useState(params.get('q') ?? '');
  const tenants = useTenants();
  const users = useUsers({ tenantId: tenantFilter ? Number(tenantFilter) : undefined, role: roleFilter || undefined, q: q || undefined });
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [draft, setDraft] = useState<{ name: string; role: Role; tenantId: string } | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [invite, setInvite] = useState<{ email: string; name: string; role: Role; tenantId: string }>({ email: '', name: '', role: 'FLEET_MANAGER', tenantId: params.get('tenantId') ?? '' });
  const [confirm, setConfirm] = useState<'disable' | 'reset' | null>(null);

  const rows = users.data?.items ?? [];
  const tenantRows = tenants.data?.items ?? [];
  const tenantName = (id: number | null) => (id === null ? '-' : tenantRows.find((t) => t.tenantId === id)?.name ?? `#${id}`);
  const selected = rows.find((u) => u.userId === selectedId) ?? null;
  useEffect(() => {
    setDraft(selected ? { name: selected.name, role: selected.role, tenantId: selected.tenantId === null ? '' : String(selected.tenantId) } : null);
  }, [selected]);

  const refresh = () => void queryCache.invalidateQueries({ queryKey: ['admin', 'users'] });

  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) => ApiClient.patch<AdminUserDto>(`/admin/users/${id}`, body),
    onSuccess: (data, { body }) => {
      toast(body.status === 'DISABLED' ? `User ${data.email} disabled` : body.status === 'ACTIVE' ? `User ${data.email} enabled` : 'User saved');
      refresh();
      setConfirm(null);
    },
  });
  const reset = useMutation({
    mutationFn: (id: number) => ApiClient.post(`/admin/users/${id}/reset-password`),
    onSuccess: () => {
      toast(`Temporary password sent to ${selected?.email ?? 'the user'}`);
      setConfirm(null);
    },
  });
  const create = useMutation({
    mutationFn: (body: { email: string; name: string; role: Role; tenantId: number | null }) => ApiClient.post<AdminUserDto>('/admin/users', body),
    onSuccess: (data) => {
      toast(`Invitation sent to ${data.email}`);
      setInviteOpen(false);
      setInvite({ email: '', name: '', role: 'FLEET_MANAGER', tenantId: '' });
      refresh();
      setSelectedId(data.userId);
    },
  });

  const columns: Column<AdminUserDto>[] = [
    { key: 'email', header: 'Email', render: (u) => <b>{u.email}</b> },
    { key: 'name', header: 'Name' },
    { key: 'role', header: 'Role' },
    { key: 'tenant', header: 'Tenant', render: (u) => tenantName(u.tenantId) },
    { key: 'status', header: 'Status', render: (u) => <StatusChip value={u.status} /> },
    { key: 'lastLogin', header: 'Last login', render: (u) => formatDateTime(u.lastLoginAt) },
  ];
  const patchErrors = fieldErrors(patch.error);
  const inviteErrors = fieldErrors(create.error);
  const draftIsStaff = draft ? STAFF.includes(draft.role) : false;
  const inviteIsStaff = STAFF.includes(invite.role);

  return (
    <Box>
      <AdminTabs />
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 1.7fr) minmax(300px, 1fr)' }, gap: 1.25, alignItems: 'start' }}>
        <Card title="Users and roles" note="GET /admin/users?tenantId&role&status&q" testId="users-card">
          <Box sx={{ display: 'flex', gap: 1, mb: 1, alignItems: 'center', flexWrap: 'wrap' }}>
            <Select value={tenantFilter} onChange={(event) => setTenantFilter(event.target.value)} displayEmpty sx={{ height: 32, fontSize: 12, minWidth: 170 }} data-testid="users-tenant-filter">
              <MenuItem value="">Tenant: all</MenuItem>
              {tenantRows.map((t) => (
                <MenuItem key={t.tenantId} value={String(t.tenantId)}>
                  {t.name}
                </MenuItem>
              ))}
            </Select>
            <Select value={roleFilter} onChange={(event) => setRoleFilter(event.target.value)} displayEmpty sx={{ height: 32, fontSize: 12, minWidth: 130 }}>
              <MenuItem value="">Role: all</MenuItem>
              {ROLES.map((r) => (
                <MenuItem key={r} value={r}>
                  {r}
                </MenuItem>
              ))}
            </Select>
            <TextField placeholder="Search email or name..." value={q} onChange={(event) => setQ(event.target.value)} sx={{ width: 200 }} inputProps={{ 'aria-label': 'Search users' }} />
            <Button variant="contained" onClick={() => setInviteOpen(true)} data-testid="invite-user">
              Invite user
            </Button>
          </Box>
          {users.isLoading ? <Loading /> : users.error ? <ErrorNote error={users.error} /> : <DataTable columns={columns} rows={rows} rowKey={(u) => u.userId} selectedKey={selectedId} onRowClick={(u) => setSelectedId(u.userId)} testId="users-table" />}
          <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
            staff roles have no tenant, tenant roles must have one; the last SYS_ADMIN cannot be disabled (BR-07)
          </Typography>
        </Card>
        <Card title={selected ? `User: ${selected.email}` : 'User'} testId="user-panel">
          {selected && draft ? (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25 }}>
              <TextField label="Name" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} error={Boolean(patchErrors.name)} helperText={patchErrors.name} />
              <FormControl>
                <InputLabel id="user-role">Role</InputLabel>
                <Select labelId="user-role" label="Role" value={draft.role} onChange={(event) => setDraft({ ...draft, role: event.target.value as Role, tenantId: STAFF.includes(event.target.value as Role) ? '' : draft.tenantId })}>
                  {ROLES.map((r) => (
                    <MenuItem key={r} value={r}>
                      {r}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
              <FormControl error={Boolean(patchErrors.tenantId)} disabled={draftIsStaff}>
                <InputLabel id="user-tenant">Tenant (required for tenant roles)</InputLabel>
                <Select labelId="user-tenant" label="Tenant (required for tenant roles)" value={draft.tenantId} onChange={(event) => setDraft({ ...draft, tenantId: event.target.value })}>
                  <MenuItem value="">- none (staff) -</MenuItem>
                  {tenantRows.map((t) => (
                    <MenuItem key={t.tenantId} value={String(t.tenantId)}>
                      {t.name}
                    </MenuItem>
                  ))}
                </Select>
                {patchErrors.tenantId && <Typography sx={{ color: '#b42318', fontSize: 11.5, mt: 0.5 }}>{patchErrors.tenantId}</Typography>}
              </FormControl>
              <Box sx={{ fontSize: 12 }}>
                Status <StatusChip value={selected.status} /> {'·'} last login {formatDateTime(selected.lastLoginAt)}
                {selected.mustChangePassword ? ' · must change password' : ''}
              </Box>
              {patch.error && !Object.keys(patchErrors).length ? <ErrorNote error={patch.error} /> : null}
              {reset.error ? <ErrorNote error={reset.error} /> : null}
              <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
                <Button
                  variant="contained"
                  disabled={patch.isPending}
                  onClick={() =>
                    patch.mutate({
                      id: selected.userId,
                      body: { name: draft.name, role: draft.role, tenantId: draft.tenantId === '' ? null : Number(draft.tenantId) },
                    })
                  }
                >
                  Save
                </Button>
                <Button variant="outlined" onClick={() => setConfirm('reset')} data-testid="reset-password">
                  Reset password
                </Button>
                {selected.status === 'DISABLED' ? (
                  <Button variant="outlined" onClick={() => patch.mutate({ id: selected.userId, body: { status: 'ACTIVE' } })}>
                    Enable
                  </Button>
                ) : (
                  <Button variant="outlined" color="error" sx={{ borderWidth: 1.5, fontWeight: 600 }} onClick={() => setConfirm('disable')} data-testid="disable-user">
                    Disable
                  </Button>
                )}
              </Box>
              <Typography variant="caption">PATCH /admin/users/{'{id}'}; POST /admin/users/{'{id}'}/reset-password sends a temporary password by email (202)</Typography>
            </Box>
          ) : (
            <Typography variant="caption">Select a user in the table to edit it.</Typography>
          )}
        </Card>
      </Box>

      <ConfirmDialog open={confirm === 'disable'} title={`Disable ${selected?.email ?? ''}?`} confirmLabel="Disable" busy={patch.isPending} onCancel={() => setConfirm(null)} onConfirm={() => selected && patch.mutate({ id: selected.userId, body: { status: 'DISABLED' } })}>
        The user cannot sign in until re-enabled; refresh tokens are revoked. The last active SYS_ADMIN cannot be disabled (BR-07).
        {patch.error ? <ErrorNote error={patch.error} /> : null}
      </ConfirmDialog>
      <ConfirmDialog open={confirm === 'reset'} title={`Reset the password of ${selected?.email ?? ''}?`} confirmLabel="Reset password" destructive={false} busy={reset.isPending} onCancel={() => setConfirm(null)} onConfirm={() => selected && reset.mutate(selected.userId)}>
        A temporary password is emailed and the user must change it at the next login.
      </ConfirmDialog>

      <Dialog open={inviteOpen} onClose={() => setInviteOpen(false)} maxWidth="xs" fullWidth>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate({ email: invite.email.trim(), name: invite.name.trim(), role: invite.role, tenantId: inviteIsStaff || invite.tenantId === '' ? null : Number(invite.tenantId) });
          }}
        >
          <DialogTitle>Invite user</DialogTitle>
          <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.25, pt: '8px !important' }}>
            <TextField label="Email" type="email" value={invite.email} onChange={(event) => setInvite({ ...invite, email: event.target.value })} required autoFocus error={Boolean(inviteErrors.email)} helperText={inviteErrors.email} inputProps={{ 'data-testid': 'invite-email' }} />
            <TextField label="Name" value={invite.name} onChange={(event) => setInvite({ ...invite, name: event.target.value })} required inputProps={{ 'data-testid': 'invite-name' }} />
            <FormControl>
              <InputLabel id="invite-role">Role</InputLabel>
              <Select labelId="invite-role" label="Role" value={invite.role} onChange={(event) => setInvite({ ...invite, role: event.target.value as Role, tenantId: STAFF.includes(event.target.value as Role) ? '' : invite.tenantId })}>
                {ROLES.map((r) => (
                  <MenuItem key={r} value={r}>
                    {r}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControl disabled={inviteIsStaff} error={Boolean(inviteErrors.tenantId)}>
              <InputLabel id="invite-tenant">Tenant</InputLabel>
              <Select labelId="invite-tenant" label="Tenant" value={invite.tenantId} onChange={(event) => setInvite({ ...invite, tenantId: event.target.value })}>
                <MenuItem value="">{inviteIsStaff ? '- none (staff) -' : '- select -'}</MenuItem>
                {tenantRows.map((t) => (
                  <MenuItem key={t.tenantId} value={String(t.tenantId)}>
                    {t.name}
                  </MenuItem>
                ))}
              </Select>
              {inviteErrors.tenantId && <Typography sx={{ color: '#b42318', fontSize: 11.5, mt: 0.5 }}>{inviteErrors.tenantId}</Typography>}
            </FormControl>
            {create.error && !Object.keys(inviteErrors).length ? <ErrorNote error={create.error} /> : null}
            <Typography variant="caption">POST /admin/users: a temporary password is emailed; the first login opens the change-password dialog.</Typography>
          </DialogContent>
          <DialogActions sx={{ px: 2, pb: 1.5 }}>
            <Button variant="outlined" onClick={() => setInviteOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="contained" disabled={create.isPending || !invite.email || !invite.name || (!inviteIsStaff && invite.tenantId === '')} data-testid="invite-send">
              Send
            </Button>
          </DialogActions>
        </form>
      </Dialog>
    </Box>
  );
}
