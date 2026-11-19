// BillingPage (report section 5.1): the tenant's monthly invoice from measured usage, the usage
// against the plan's allowances, and for staff every tenant's bill; SYS_ADMIN edits the price list.
// GET /billing/invoice, GET /billing/summary, GET and PUT /billing/prices.
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import TextField from '@mui/material/TextField';
import Select from '@mui/material/Select';
import MenuItem from '@mui/material/MenuItem';
import Typography from '@mui/material/Typography';
import LinearProgress from '@mui/material/LinearProgress';
import Alert from '@mui/material/Alert';
import { useAuth } from '../auth/AuthProvider';
import { ApiClient } from '../api/ApiClient';
import { queryCache } from '../api/queries';
import { Card, DataTable, ErrorNote, KpiTile, Loading, type Column } from '../components/common';
import { StatusChip } from '../components/StatusChip';
import { useToast } from '../components/Toast';
import { formatDate, formatNumber } from '../util/format';

interface Line { code: string; description: string; quantity: number; unit: string; unitCents: number; amountCents: number }

/** "1 month", "2 vehicles", "3 × 1,000 clips", "0.001 GB-month". */
function quantityText(l: Line): string {
  const n = formatNumber(l.quantity, l.code === 'STORAGE' ? 3 : 0);
  if (/^[\d,]+ /.test(l.unit)) return `${n} × ${l.unit}`;
  if (l.unit === 'vehicle' || l.unit === 'month') return `${n} ${l.unit}${l.quantity === 1 ? '' : 's'}`;
  return `${n} ${l.unit}`;
}
interface Invoice {
  tenantId: number; tenant: string; plan: string; month: string; from: string; to: string; running: boolean;
  usage: { vehicles: number; alerts: number; clips: number; emails: number; storageGb: number; notMeasured: string[] };
  allowances: { vehicles: number; clips: number; emails: number };
  lines: Line[]; totalCents: number; projectedTotalCents: number;
}
interface SummaryRow { tenantId: number; tenant: string; plan: string; status: string; vehicles: number; clips: number; emails: number; alerts: number; totalCents: number; projectedTotalCents: number }
interface Summary { month: string; running: boolean; tenants: SummaryRow[]; totalCents: number; projectedTotalCents: number }
interface Price { plan: string; monthlyFeeCents: number; includedVehicles: number; extraVehicleCents: number; includedClips: number; per1000ClipsCents: number; includedEmails: number; per100EmailsCents: number; storageGbCents: number; updatedAt: string }

const usd = (cents: number) => `$${(cents / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function lastMonths(count: number): string[] {
  const now = new Date();
  return Array.from({ length: count }, (_, i) => {
    const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - i, 1));
    return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
  });
}

function Meter({ label, used, included }: { label: string; used: number; included: number }) {
  const pct = included > 0 ? Math.min(100, (used / included) * 100) : used > 0 ? 100 : 0;
  const over = used > included;
  return (
    <Box sx={{ mb: 1 }}>
      <Box sx={{ display: 'flex', fontSize: 12.5, mb: 0.25 }}>
        <b>{label}</b>
        <Box sx={{ ml: 'auto', color: over ? '#b42318' : 'inherit' }}>
          {formatNumber(used)} of {formatNumber(included)} included{over ? ` (${formatNumber(used - included)} over)` : ''}
        </Box>
      </Box>
      <LinearProgress variant="determinate" value={pct} color={over ? 'error' : 'primary'} sx={{ height: 7, borderRadius: 4 }} />
    </Box>
  );
}

const PRICE_FIELDS: Array<{ key: keyof Price; label: string; money: boolean }> = [
  { key: 'monthlyFeeCents', label: 'Monthly fee ($)', money: true },
  { key: 'includedVehicles', label: 'Vehicles included', money: false },
  { key: 'extraVehicleCents', label: 'Per extra vehicle ($)', money: true },
  { key: 'includedClips', label: 'Clips included', money: false },
  { key: 'per1000ClipsCents', label: 'Per 1,000 clips ($)', money: true },
  { key: 'includedEmails', label: 'Emails included', money: false },
  { key: 'per100EmailsCents', label: 'Per 100 emails ($)', money: true },
  { key: 'storageGbCents', label: 'Per GB-month ($)', money: true },
];

function PriceEditor({ price }: { price: Price }) {
  const { toast } = useToast();
  const toDraft = (p: Price) => Object.fromEntries(PRICE_FIELDS.map((f) => [f.key, f.money ? ((p[f.key] as number) / 100).toFixed(2) : String(p[f.key])]));
  const [draft, setDraft] = useState<Record<string, string>>(toDraft(price));
  useEffect(() => setDraft(toDraft(price)), [price]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({
    mutationFn: () =>
      ApiClient.put<Price>(`/billing/prices/${price.plan}`, Object.fromEntries(PRICE_FIELDS.map((f) => [f.key, f.money ? Math.round(Number(draft[f.key]) * 100) : Math.round(Number(draft[f.key]))]))),
    onSuccess: () => {
      toast(`Prices of ${price.plan} saved`);
      void queryCache.invalidateQueries({ queryKey: ['billing'] });
    },
  });
  const invalid = PRICE_FIELDS.some((f) => draft[f.key] === '' || Number.isNaN(Number(draft[f.key])) || Number(draft[f.key]) < 0);
  return (
    <Box sx={{ border: '1px solid #e4e1d8', borderRadius: 1, p: 1 }} data-testid={`price-${price.plan}`}>
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 0.75 }}>
        <b>{price.plan}</b>
        <Typography variant="caption" sx={{ ml: 1 }}>updated {formatDate(price.updatedAt)}</Typography>
        <Button size="small" variant="contained" sx={{ ml: 'auto' }} disabled={invalid || save.isPending} onClick={() => save.mutate()}>
          Save
        </Button>
      </Box>
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'repeat(2, 1fr)', md: 'repeat(4, 1fr)' }, gap: 0.75 }}>
        {PRICE_FIELDS.map((f) => (
          <TextField key={f.key} label={f.label} value={draft[f.key]} onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })} inputProps={{ inputMode: 'decimal' }} size="small" />
        ))}
      </Box>
      {save.error ? <ErrorNote error={save.error} /> : null}
    </Box>
  );
}

export function BillingPage() {
  const { tenantId, isStaff, user } = useAuth();
  const isAdmin = user?.role === 'SYS_ADMIN';
  const months = useMemo(() => lastMonths(6), []);
  const [month, setMonth] = useState(months[0]);

  const invoice = useQuery<Invoice>({
    queryKey: ['billing', 'invoice', tenantId, month],
    queryFn: () => ApiClient.get<Invoice>('/billing/invoice', { tenantId: tenantId ?? undefined, month }),
    enabled: tenantId !== null,
  });
  const summary = useQuery<Summary>({
    queryKey: ['billing', 'summary', month],
    queryFn: () => ApiClient.get<Summary>('/billing/summary', { month }),
    enabled: isStaff,
  });
  const prices = useQuery<Price[]>({ queryKey: ['billing', 'prices'], queryFn: () => ApiClient.get<Price[]>('/billing/prices') });

  const lineColumns: Column<Line>[] = [
    { key: 'description', header: 'Item', render: (l) => l.description },
    { key: 'quantity', header: 'Quantity', align: 'right', render: (l) => quantityText(l) },
    { key: 'unitCents', header: 'Unit price', align: 'right', render: (l) => usd(l.unitCents) },
    { key: 'amountCents', header: 'Amount', align: 'right', render: (l) => <b>{usd(l.amountCents)}</b> },
  ];
  const summaryColumns: Column<SummaryRow>[] = [
    { key: 'tenant', header: 'Tenant', render: (r) => <b>{r.tenant}</b> },
    { key: 'plan', header: 'Plan' },
    { key: 'status', header: 'Status', render: (r) => <StatusChip value={r.status} /> },
    { key: 'vehicles', header: 'Vehicles', align: 'right' },
    { key: 'clips', header: 'Clips', align: 'right', render: (r) => formatNumber(r.clips) },
    { key: 'emails', header: 'Emails', align: 'right', render: (r) => formatNumber(r.emails) },
    { key: 'totalCents', header: 'Billed', align: 'right', render: (r) => <b>{usd(r.totalCents)}</b> },
    { key: 'projectedTotalCents', header: 'Projected', align: 'right', render: (r) => usd(r.projectedTotalCents) },
  ];

  const inv = invoice.data;
  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
        <Select value={month} onChange={(e) => setMonth(e.target.value)} sx={{ height: 32, fontSize: 12.5, minWidth: 140 }} data-testid="billing-month">
          {months.map((m, i) => (
            <MenuItem key={m} value={m}>{m}{i === 0 ? ' (this month)' : ''}</MenuItem>
          ))}
        </Select>
        <Typography variant="caption">Usage is measured from the platform's data: vehicles (C2), clips classified (C4), alerts and emails (C3), clip storage (C2).</Typography>
      </Box>

      {tenantId === null ? (
        <Alert severity="info" sx={{ mb: 1.25 }}>Pick a tenant in the selector at the top to see its invoice.</Alert>
      ) : invoice.isLoading ? <Loading /> : invoice.error ? <ErrorNote error={invoice.error} /> : inv ? (
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 1.6fr) minmax(300px, 1fr)' }, gap: 1.25, alignItems: 'start', mb: 1.25 }}>
          <Card title={`Invoice ${inv.month} - ${inv.tenant}`} note={`${inv.plan} plan · ${inv.running ? `to date (${formatDate(inv.to)})` : 'final'}`} testId="invoice-card">
            <Box sx={{ display: 'flex', gap: 1, mb: 1, flexWrap: 'wrap' }}>
              <KpiTile label={inv.running ? 'Billed to date' : 'Total'} value={usd(inv.totalCents)} testId="invoice-total" />
              {inv.running && <KpiTile label="Projected for the month" value={usd(inv.projectedTotalCents)} testId="invoice-projected" />}
              <KpiTile label="Alerts this month" value={formatNumber(inv.usage.alerts)} hint="included" />
            </Box>
            <DataTable columns={lineColumns} rows={inv.lines} rowKey={(l) => l.code} dense testId="invoice-lines" />
            <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 0.75, fontSize: 14 }} data-testid="invoice-sum">
              Total&nbsp;<b>{usd(inv.totalCents)}</b>&nbsp;USD
            </Box>
            {inv.usage.notMeasured.length > 0 && (
              <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
                Not measured in this installation: {inv.usage.notMeasured.join(', ')} (the component that stores them is not running).
              </Typography>
            )}
          </Card>
          <Card title="Usage against the plan" testId="usage-card">
            <Meter label="Vehicles" used={inv.usage.vehicles} included={inv.allowances.vehicles} />
            <Meter label="Clips classified" used={inv.usage.clips} included={inv.allowances.clips} />
            <Meter label="Alert emails sent" used={inv.usage.emails} included={inv.allowances.emails} />
            <Typography sx={{ fontSize: 12.5 }}>Clip storage: <b>{inv.usage.storageGb.toFixed(3)} GB</b></Typography>
          </Card>
        </Box>
      ) : null}

      {isStaff && (
        <Card title={`All tenants - ${month}`} note="GET /billing/summary" testId="billing-summary" sx={{ mb: 1.25 }}>
          {summary.isLoading ? <Loading /> : summary.error ? <ErrorNote error={summary.error} /> : summary.data ? (
            <>
              <DataTable columns={summaryColumns} rows={summary.data.tenants} rowKey={(r) => r.tenantId} dense testId="billing-summary-table" />
              <Typography sx={{ fontSize: 13, mt: 0.75 }} data-testid="billing-revenue">
                Platform revenue {summary.data.running ? 'to date' : ''}: <b>{usd(summary.data.totalCents)}</b>
                {summary.data.running ? ` · projected ${usd(summary.data.projectedTotalCents)}` : ''} (active tenants)
              </Typography>
            </>
          ) : null}
        </Card>
      )}

      <Card title="Price list" note={isAdmin ? 'PUT /billing/prices/{plan} · audited as PRICE_UPDATE' : 'GET /billing/prices'} testId="price-list">
        {prices.isLoading ? <Loading /> : prices.error ? <ErrorNote error={prices.error} /> : isAdmin ? (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
            {(prices.data ?? []).map((p) => <PriceEditor key={p.plan} price={p} />)}
          </Box>
        ) : (
          <DataTable
            columns={[
              { key: 'plan', header: 'Plan', render: (p: Price) => <b>{p.plan}</b> },
              { key: 'monthlyFeeCents', header: 'Monthly fee', align: 'right', render: (p: Price) => usd(p.monthlyFeeCents) },
              { key: 'includedVehicles', header: 'Vehicles included', align: 'right' },
              { key: 'extraVehicleCents', header: 'Extra vehicle', align: 'right', render: (p: Price) => usd(p.extraVehicleCents) },
              { key: 'includedClips', header: 'Clips included', align: 'right', render: (p: Price) => formatNumber(p.includedClips) },
              { key: 'per1000ClipsCents', header: 'Per 1,000 clips', align: 'right', render: (p: Price) => usd(p.per1000ClipsCents) },
              { key: 'includedEmails', header: 'Emails included', align: 'right', render: (p: Price) => formatNumber(p.includedEmails) },
              { key: 'per100EmailsCents', header: 'Per 100 emails', align: 'right', render: (p: Price) => usd(p.per100EmailsCents) },
              { key: 'storageGbCents', header: 'Per GB-month', align: 'right', render: (p: Price) => usd(p.storageGbCents) },
            ]}
            rows={prices.data ?? []}
            rowKey={(p) => p.plan}
            dense
          />
        )}
      </Card>
    </Box>
  );
}
