// DeploymentCard: which version each C4 classifier worker runs (GET /models/{id}/deployment),
// with its state chip (READY / LOADING / FAILED), load time, whether the SHA-256 of the
// downloaded artifact matched the registry, and the last heartbeat. A model.deployed message
// patches the row at once; the query also refetches every 10 s.
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';
import { queryCache, queryKeys, useDeployment } from '../api/queries';
import type { DeploymentResponse, DeploymentWorker, LiveMessage } from '../api/types';
import { useLiveHandler } from '../live/useLiveEvents';
import { colors } from '../theme';
import { ago, formatTime, shortSha } from '../util/format';
import { Card, DataTable, ErrorNote, Loading, type Column } from './common';
import { StatusChip } from './StatusChip';

function Verified({ worker }: { worker: DeploymentWorker }) {
  if (worker.state === 'LOADING') return <Typography variant="caption">checking...</Typography>;
  const ok = worker.sha256Verified;
  return (
    <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5, fontSize: 12, color: ok ? '#1e7b34' : colors.destructive }} title={worker.sha256 ?? ''} data-testid="worker-sha-verified" data-verified={ok ? 'yes' : 'no'}>
      {ok ? <CheckCircleOutlineIcon sx={{ fontSize: 15 }} /> : <ErrorOutlineIcon sx={{ fontSize: 15 }} />}
      {ok ? `SHA-256 verified (${shortSha(worker.sha256)})` : 'not verified'}
    </Box>
  );
}

export function DeploymentCard({ modelId, modelName }: { modelId: number | null; modelName: string }) {
  const deployment = useDeployment(modelId);

  useLiveHandler('model.deployed', (message: LiveMessage) => {
    const report = message as LiveMessage & DeploymentWorker & { modelId?: number };
    if (modelId === null || report.modelId !== modelId) return;
    queryCache.setQueryData<DeploymentResponse>(queryKeys.deployment(modelId), (current) => {
      if (!current) return current;
      const row: DeploymentWorker = {
        workerId: report.workerId,
        modelVersion: report.modelVersion,
        state: report.state,
        loadedAt: report.loadedAt,
        sha256: report.sha256,
        sha256Verified: report.sha256Verified,
        lastHeartbeatAt: report.lastHeartbeatAt,
        detail: report.detail,
        stale: false,
        runsActiveVersion: report.modelVersion === current.activeVersion,
      };
      const others = current.workers.filter((w) => w.workerId !== row.workerId);
      return { ...current, workers: [...others, row].sort((a, b) => a.workerId.localeCompare(b.workerId)) };
    });
  });

  const data = deployment.data;
  const columns: Column<DeploymentWorker>[] = [
    { key: 'workerId', header: 'Worker', render: (w) => <b>{w.workerId}</b> },
    {
      key: 'modelVersion',
      header: 'Runs version',
      render: (w) => (
        <span data-testid="worker-version">
          {w.modelVersion ?? '-'}
          {data?.activeVersion && w.modelVersion && w.modelVersion !== data.activeVersion && w.state !== 'LOADING' ? (
            <Typography component="span" variant="caption" sx={{ ml: 0.5 }}>
              (active is {data.activeVersion})
            </Typography>
          ) : null}
        </span>
      ),
    },
    { key: 'state', header: 'State', render: (w) => <span data-testid="worker-state"><StatusChip value={w.state} /></span> },
    { key: 'loadedAt', header: 'Loaded', render: (w) => formatTime(w.loadedAt) },
    { key: 'sha256Verified', header: 'Artifact', render: (w) => <Verified worker={w} /> },
    {
      key: 'lastHeartbeatAt',
      header: 'Heartbeat',
      render: (w) => (
        <span style={{ color: w.stale ? colors.destructive : undefined }}>
          {ago(w.lastHeartbeatAt)} ago{w.stale ? ' (stale)' : ''}
        </span>
      ),
    },
  ];

  return (
    <Card title="Deployment" note="GET /models/{id}/deployment · model.deployed" testId="deployment-card">
      {deployment.isLoading ? (
        <Loading />
      ) : deployment.error ? (
        <ErrorNote error={deployment.error} />
      ) : (
        <>
          <DataTable
            columns={columns}
            rows={data?.workers ?? []}
            rowKey={(w) => w.workerId}
            empty={`No classifier worker has reported for ${modelName} yet`}
            testId="deployment-table"
          />
          {(data?.workers ?? [])
            .filter((w) => w.detail)
            .map((w) => (
              <Typography key={w.workerId} variant="caption" sx={{ display: 'block', mt: 0.5 }} data-testid="worker-detail">
                {w.workerId}: {w.detail}
              </Typography>
            ))}
        </>
      )}
      <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
        activation {'→'} model.activated (EventBus, MQTT scicp/v1/platform/model/activated) {'→'} the worker downloads the artifact through C1, checks the SHA-256 and reports LOADING, then READY or FAILED (polls every 30 s as a fallback)
      </Typography>
    </Card>
  );
}
