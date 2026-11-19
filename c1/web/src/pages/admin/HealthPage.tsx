// HealthPage (M8): one row per part of the platform, probed by the API every 10 s.
// GET /admin/health -> {status, down, parts[{name, kind, state, latencyMs, detail}], uptimeSeconds}.
import { useQuery } from '@tanstack/react-query';
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';
import { ApiClient } from '../../api/ApiClient';
import { Card, DataTable, ErrorNote, Loading, type Column } from '../../components/common';
import { StatusChip } from '../../components/StatusChip';
import { ago, formatDateTime } from '../../util/format';
import { AdminTabs } from './AdminTabs';

interface Part {
  name: string;
  kind: string;
  state: 'UP' | 'DOWN' | 'STALE';
  latencyMs: number | null;
  detail: string | null;
  lastSeenAt?: string | null;
}

interface HealthReport {
  status: 'UP' | 'DEGRADED';
  down: string[];
  parts: Part[];
  uptimeSeconds: number;
  checkedAt: string;
}

function uptime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min`;
}

export function HealthPage() {
  const health = useQuery<HealthReport>({
    queryKey: ['admin', 'health'],
    queryFn: () => ApiClient.get<HealthReport>('/admin/health'),
    refetchInterval: 10_000,
  });

  const columns: Column<Part>[] = [
    { key: 'state', header: 'State', render: (p) => <StatusChip value={p.state} /> },
    { key: 'kind', header: 'Part', render: (p) => <b>{p.kind}</b> },
    { key: 'name', header: 'Name' },
    { key: 'latencyMs', header: 'Latency', align: 'right', render: (p) => (p.latencyMs === null ? '-' : `${p.latencyMs} ms`) },
    { key: 'detail', header: 'Detail', render: (p) => (p.lastSeenAt ? `${p.detail ?? ''} · heartbeat ${ago(p.lastSeenAt)}` : p.detail ?? '') },
  ];

  const report = health.data;
  return (
    <Box>
      <AdminTabs />
      <Card
        title="Platform health"
        note="GET /admin/health · probed every 10 s"
        testId="health-card"
        actions={report ? <StatusChip value={report.status} /> : undefined}
      >
        {health.isLoading ? <Loading /> : health.error ? <ErrorNote error={health.error} /> : report ? (
          <>
            <Typography sx={{ fontSize: 12.5, mb: 1 }} data-testid="health-summary">
              {report.status === 'UP'
                ? `All ${report.parts.length} parts are up.`
                : `${report.down.length} of ${report.parts.length} parts need attention: ${report.down.join(', ')}.`}{' '}
              API uptime {uptime(report.uptimeSeconds)} {'·'} checked {formatDateTime(report.checkedAt)}
            </Typography>
            <DataTable columns={columns} rows={report.parts} rowKey={(p) => p.name} dense testId="health-table" />
            <Typography variant="caption" sx={{ display: 'block', mt: 0.75 }}>
              MySQL is probed with SELECT 1, MongoDB with a ping, the MQTT broker with a TCP connect, C2 and C3 with their GET /health,
              and each classifier worker by its last heartbeat (stale after 90 s).
            </Typography>
          </>
        ) : null}
      </Card>
    </Box>
  );
}
