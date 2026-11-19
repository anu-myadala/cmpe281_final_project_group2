// AuditLogPage (M8, BR-12): every write of the platform with its actor, newest first.
// GET /admin/audit?actor&entity&action&from&to&page&size. Filters on the left of the table,
// the before and after values of the selected row on the right. SYS_ADMIN only.
import { useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import TextField from '@mui/material/TextField';
import Select from '@mui/material/Select';
import MenuItem from '@mui/material/MenuItem';
import Typography from '@mui/material/Typography';
import { ApiClient } from '../../api/ApiClient';
import { Card, DataTable, ErrorNote, Loading, type Column } from '../../components/common';
import { formatDateTime } from '../../util/format';
import { AdminTabs } from './AdminTabs';

interface AuditRow {
  auditId: number;
  ts: string;
  actorUserId: number | null;
  actor: string;
  tenantId: number | null;
  tenant: string | null;
  action: string;
  entity: string;
  entityId: string | null;
  before: unknown;
  after: unknown;
  ip: string | null;
}

interface AuditPage {
  items: AuditRow[];
  page: number;
  size: number;
  total: number;
  facets: { actions: string[]; entities: string[] };
}

const SIZE = 25;

function Json({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <Typography variant="caption">none</Typography>;
  return (
    <Box component="pre" sx={{ fontSize: 11, bgcolor: '#f5f4f0', p: 1, borderRadius: 1, m: 0, maxHeight: 260, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
      {JSON.stringify(value, null, 2)}
    </Box>
  );
}

export function AuditLogPage() {
  const [actor, setActor] = useState('');
  const [action, setAction] = useState('');
  const [entity, setEntity] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const query = {
    actor: actor.trim() || undefined,
    action: action || undefined,
    entity: entity || undefined,
    from: from || undefined,
    to: to || undefined,
    page,
    size: SIZE,
  };
  const audit = useQuery<AuditPage>({
    queryKey: ['admin', 'audit', query],
    queryFn: () => ApiClient.get<AuditPage>('/admin/audit', query),
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  });

  const rows = audit.data?.items ?? [];
  const selected = rows.find((r) => r.auditId === selectedId) ?? null;
  const total = audit.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / SIZE));
  const resetPage = () => {
    setPage(1);
    setSelectedId(null);
  };

  const columns: Column<AuditRow>[] = [
    { key: 'auditId', header: '#', align: 'right', width: 60 },
    { key: 'ts', header: 'Time', render: (r) => formatDateTime(r.ts) },
    { key: 'actor', header: 'Actor', render: (r) => r.actor },
    { key: 'action', header: 'Action', render: (r) => <b>{r.action}</b> },
    { key: 'entity', header: 'Entity', render: (r) => `${r.entity}${r.entityId ? ` ${r.entityId}` : ''}` },
    { key: 'tenant', header: 'Tenant', render: (r) => r.tenant ?? '-' },
  ];

  return (
    <Box>
      <AdminTabs />
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 1.8fr) minmax(300px, 1fr)' }, gap: 1.25, alignItems: 'start' }}>
        <Card title="Audit log" note="GET /admin/audit?actor&entity&action&from&to&page" testId="audit-card">
          <Box sx={{ display: 'flex', gap: 1, mb: 1, alignItems: 'center', flexWrap: 'wrap' }}>
            <TextField placeholder="Actor email..." value={actor} onChange={(e) => { setActor(e.target.value); resetPage(); }} sx={{ width: 180 }} inputProps={{ 'aria-label': 'Actor email', 'data-testid': 'audit-actor' }} />
            <Select value={action} onChange={(e) => { setAction(e.target.value); resetPage(); }} displayEmpty sx={{ height: 32, fontSize: 12, minWidth: 150 }} data-testid="audit-action">
              <MenuItem value="">Action: all</MenuItem>
              {(audit.data?.facets.actions ?? []).map((a) => (
                <MenuItem key={a} value={a}>{a}</MenuItem>
              ))}
            </Select>
            <Select value={entity} onChange={(e) => { setEntity(e.target.value); resetPage(); }} displayEmpty sx={{ height: 32, fontSize: 12, minWidth: 140 }}>
              <MenuItem value="">Entity: all</MenuItem>
              {(audit.data?.facets.entities ?? []).map((a) => (
                <MenuItem key={a} value={a}>{a}</MenuItem>
              ))}
            </Select>
            <TextField label="From" type="date" value={from} onChange={(e) => { setFrom(e.target.value); resetPage(); }} InputLabelProps={{ shrink: true }} sx={{ width: 145 }} />
            <TextField label="To" type="date" value={to} onChange={(e) => { setTo(e.target.value); resetPage(); }} InputLabelProps={{ shrink: true }} sx={{ width: 145 }} />
            <Button variant="outlined" onClick={() => { setActor(''); setAction(''); setEntity(''); setFrom(''); setTo(''); resetPage(); }}>
              Clear
            </Button>
          </Box>
          {audit.isLoading ? <Loading /> : audit.error ? <ErrorNote error={audit.error} /> : (
            <DataTable columns={columns} rows={rows} rowKey={(r) => r.auditId} selectedKey={selectedId} onRowClick={(r) => setSelectedId(r.auditId)} empty="No audit rows match the filters" dense testId="audit-table" />
          )}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 0.75 }}>
            <Typography variant="caption" data-testid="audit-total">
              {total} row{total === 1 ? '' : 's'} {'·'} page {page} of {pages} {'·'} refreshes every 15 s
            </Typography>
            <Box sx={{ ml: 'auto', display: 'flex', gap: 0.5 }}>
              <Button size="small" variant="outlined" disabled={page <= 1} onClick={() => { setPage(page - 1); setSelectedId(null); }}>Previous</Button>
              <Button size="small" variant="outlined" disabled={page >= pages} onClick={() => { setPage(page + 1); setSelectedId(null); }}>Next</Button>
            </Box>
          </Box>
        </Card>
        <Card title={selected ? `Audit row ${selected.auditId}: ${selected.action}` : 'Audit row'} testId="audit-detail">
          {selected ? (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
              <Typography sx={{ fontSize: 12.5 }}>
                <b>{selected.actor}</b> {'·'} {formatDateTime(selected.ts)} {'·'} {selected.entity} {selected.entityId ?? ''} {selected.tenant ? `· ${selected.tenant}` : ''} {selected.ip ? `· from ${selected.ip}` : ''}
              </Typography>
              <Typography variant="subtitle2">Before</Typography>
              <Json value={selected.before} />
              <Typography variant="subtitle2">After</Typography>
              <Json value={selected.after} />
              <Typography variant="caption">Secrets (password hashes, tokens) are never stored in the audit log.</Typography>
            </Box>
          ) : (
            <Typography variant="caption">Select a row to see the before and after values (BR-12).</Typography>
          )}
        </Card>
      </Box>
    </Box>
  );
}
