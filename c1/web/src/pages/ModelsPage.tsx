// ModelsPage: model list and VersionTable (8), the selected version with artifact, labels and
// held-out results (9), Upload new version / Set as active / Roll back / Download / Export labels (10)
// with the confirm dialog for activation and rollback (12), and the Deployment card (which
// version the classifier worker runs). Listens to model.activated and model.deployed.
import { useEffect, useMemo, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Typography from '@mui/material/Typography';
import TextField from '@mui/material/TextField';
import Checkbox from '@mui/material/Checkbox';
import FormControlLabel from '@mui/material/FormControlLabel';
import Alert from '@mui/material/Alert';
import Dialog from '@mui/material/Dialog';
import DialogTitle from '@mui/material/DialogTitle';
import DialogContent from '@mui/material/DialogContent';
import DialogActions from '@mui/material/DialogActions';
import { useAuth } from '../auth/AuthProvider';
import { useLiveHandler } from '../live/useLiveEvents';
import { queryCache, queryKeys, useModels, useVersions } from '../api/queries';
import { ApiClient, API_BASE, errorMessage, getAccessToken, isApiError } from '../api/ApiClient';
import type { ModelDto, VersionDto } from '../api/types';
import { Card, ConfirmDialog, DataTable, ErrorNote, Loading, type Column } from '../components/common';
import { StatusChip } from '../components/StatusChip';
import { DeploymentCard } from '../components/DeploymentCard';
import { MetricsTable, VersionTable, compareSemver } from '../components/VersionTable';
import { useToast } from '../components/Toast';
import { UploadVersionDialog } from './UploadVersionDialog';
import { formatBytes, formatDateTime, shortSha } from '../util/format';

const MARGIN = 0.05;

function nextVersion(versions: VersionDto[]): string {
  const top = [...versions].sort((a, b) => compareSemver(b.version, a.version))[0];
  if (!top) return '1.0.0';
  const [major, minor] = top.version.split('.').map(Number);
  return `${major}.${minor + 1}.0`;
}

export function ModelsPage() {
  const { user } = useAuth();
  const { toast } = useToast();
  const canWrite = user?.role === 'SERVICE_STAFF' || user?.role === 'SYS_ADMIN';
  const models = useModels();
  const [modelId, setModelId] = useState<number | null>(null);
  const versions = useVersions(modelId);
  const [versionId, setVersionId] = useState<number | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [newModelOpen, setNewModelOpen] = useState(false);
  const [newModel, setNewModel] = useState({ name: '', task: '' });
  const [confirm, setConfirm] = useState<'activate' | 'rollback' | null>(null);
  const [force, setForce] = useState(false);
  const [note, setNote] = useState('');

  const modelRows = models.data ?? [];
  useEffect(() => {
    if (modelId === null && modelRows.length > 0) setModelId(modelRows[0].modelId);
  }, [modelRows, modelId]);
  const model = modelRows.find((m) => m.modelId === modelId) ?? null;
  const versionRows = useMemo(() => versions.data ?? [], [versions.data]);
  useEffect(() => {
    if (versionRows.length === 0) return;
    if (versionId === null || !versionRows.some((v) => v.versionId === versionId)) {
      const preferred = [...versionRows].sort((a, b) => compareSemver(b.version, a.version)).find((v) => v.status !== 'ACTIVE') ?? versionRows[0];
      setVersionId(preferred.versionId);
    }
  }, [versionRows, versionId]);
  const selected = versionRows.find((v) => v.versionId === versionId) ?? null;
  const active = versionRows.find((v) => v.status === 'ACTIVE') ?? null;

  useLiveHandler('model.activated', () => void queryCache.invalidateQueries({ queryKey: ['models'] }));

  const refresh = () => void queryCache.invalidateQueries({ queryKey: ['models'] });

  const activate = useMutation({
    mutationFn: ({ version, forceIt, noteText }: { version: string; forceIt: boolean; noteText: string }) =>
      ApiClient.post<{ active: string; previous: string | null }>(`/models/${modelId}/versions/${version}/activate`, { force: forceIt, note: noteText || undefined }),
    onSuccess: (data) => {
      toast(`Version ${data.active} is active`);
      setConfirm(null);
      setForce(false);
      setNote('');
      refresh();
    },
  });
  const rollback = useMutation({
    mutationFn: (noteText: string) => ApiClient.post<{ active: string; previous: string | null }>(`/models/${modelId}/rollback`, { note: noteText || undefined }),
    onSuccess: (data) => {
      toast(`Rolled back: version ${data.active} is active`);
      setConfirm(null);
      setNote('');
      refresh();
    },
  });
  const createModel = useMutation({
    mutationFn: (body: { name: string; task: string }) => ApiClient.post<ModelDto>('/models', body),
    onSuccess: (data) => {
      toast(`Model ${data.name} created`);
      setNewModelOpen(false);
      setNewModel({ name: '', task: '' });
      refresh();
      setModelId(data.modelId);
    },
  });

  const download = async () => {
    if (!selected) return;
    try {
      // The API answers 302 to a presigned URL (15 min); the browser follows it to the bucket.
      const response = await fetch(`${API_BASE}/models/${modelId}/versions/${selected.version}/download`, {
        headers: { Authorization: `Bearer ${getAccessToken() ?? ''}` },
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as { error?: { message?: string } } | null;
        throw new Error(body?.error?.message ?? `Download failed (${response.status})`);
      }
      const blob = await response.blob();
      const href = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = href;
      link.download = `${model?.name ?? 'model'}-${selected.version}.pt`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(href), 2000);
      toast(`Download started: ${link.download}`);
    } catch (error) {
      toast(error instanceof TypeError ? 'The artifact storage is not reachable from the browser' : errorMessage(error), 'error');
    }
  };

  const exportLabels = async () => {
    if (!selected) return;
    try {
      const name = await ApiClient.download(`/models/${modelId}/feedback/export`, { versionId: selected.versionId, format: 'csv' }, `feedback_model${modelId}_v${selected.versionId}.csv`);
      toast(`Export ready: ${name}`);
    } catch (error) {
      toast(isApiError(error) && error.status === 404 ? 'No labels for this version (BR-11)' : errorMessage(error), 'warning');
    }
  };

  const modelColumns: Column<ModelDto>[] = [
    { key: 'name', header: 'Model', render: (m) => <b>{m.name}</b> },
    { key: 'task', header: 'Task' },
    { key: 'activeVersion', header: 'Active', render: (m) => m.activeVersion ?? '-' },
    { key: 'versions', header: 'Versions', align: 'right' },
  ];

  const br10Fails = selected && active && selected.metrics && active.metrics ? selected.metrics.macroF1 < active.metrics.macroF1 - MARGIN : false;
  const activateError = activate.error;
  const activateDetails = isApiError(activateError) ? activateError.details : {};

  return (
    <Box>
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 1.45fr) minmax(320px, 1fr)' }, gap: 1.25, alignItems: 'start' }}>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25, minWidth: 0 }}>
          <Card title="Models" note="GET /models" testId="models-card">
            {models.isLoading ? <Loading /> : models.error ? <ErrorNote error={models.error} /> : <DataTable columns={modelColumns} rows={modelRows} rowKey={(m) => m.modelId} selectedKey={modelId} onRowClick={(m) => { setModelId(m.modelId); setVersionId(null); }} testId="models-table" />}
            {canWrite && (
              <Box sx={{ mt: 0.75, display: 'flex', alignItems: 'center', gap: 1 }}>
                <Button variant="outlined" onClick={() => setNewModelOpen(true)}>
                  New model
                </Button>
                <Typography variant="caption">POST /models {'{name, task}'} {'·'} SERVICE_STAFF, SYS_ADMIN</Typography>
              </Box>
            )}
          </Card>
          <Card title={`Versions of ${model?.name ?? '...'}`} note="GET /models/{id}/versions" testId="versions-card">
            {canWrite && (
              <Box sx={{ mb: 0.75, display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap' }}>
                <Button variant="contained" onClick={() => setUploadOpen(true)} disabled={!model} data-testid="upload-new-version">
                  Upload new version
                </Button>
                <Button variant="outlined" color="error" sx={{ borderWidth: 1.5, fontWeight: 600 }} onClick={() => setConfirm('rollback')} disabled={!model || !versionRows.some((v) => v.status === 'INACTIVE')} data-testid="rollback">
                  Roll back
                </Button>
                <Typography variant="caption">roll back = the previously ACTIVE version</Typography>
              </Box>
            )}
            {versions.isLoading ? <Loading /> : versions.error ? <ErrorNote error={versions.error} /> : <VersionTable versions={versionRows} selectedId={versionId} onSelect={(v) => setVersionId(v.versionId)} />}
            <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
              states: UPLOADING {'→'} REGISTERED {'→'} ACTIVE {'↔'} INACTIVE, FAILED on checksum or metrics error; one ACTIVE per model; the chip moves live on model.activated
            </Typography>
          </Card>
          <DeploymentCard modelId={modelId} modelName={model?.name ?? 'this model'} />
        </Box>
        <Card
          title={
            selected ? (
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <span>Version {selected.version}</span>
                <StatusChip value={selected.status} />
              </Box>
            ) : (
              'Version'
            )
          }
          testId="version-panel"
        >
          {selected ? (
            <>
              <Box sx={{ fontSize: 12, lineHeight: 1.6, '& b': { display: 'inline-block', width: 92, color: '#444', fontWeight: 400 } }}>
                <b>Artifact</b> {selected.artifactUri ?? '-'}
                <br />
                <b>SHA-256</b> <span title={selected.sha256 ?? ''}>{shortSha(selected.sha256)}</span>
                <br />
                <b>Size</b> {formatBytes(selected.sizeBytes)}
                <br />
                <b>Created</b> {formatDateTime(selected.createdAt)}
                <br />
                <b>Activated</b> {formatDateTime(selected.activatedAt)}
                <br />
                <b>Notes</b> {selected.notes ?? '-'}
                <br />
                <b>Labels</b> {selected.labelCount} feedback label{selected.labelCount === 1 ? '' : 's'} (BR-11)
              </Box>
              <Typography variant="subtitle1" sx={{ mt: 1, mb: 0.5 }}>
                Held-out test results <Typography component="span" variant="caption">MetricsTable, metrics_json</Typography>
              </Typography>
              {selected.metrics ? <MetricsTable perClass={selected.metrics.perClass ?? {}} macroF1={selected.metrics.macroF1} accuracy={selected.metrics.accuracy} /> : <Typography variant="caption">No metrics recorded (the upload was not completed).</Typography>}
              <Box sx={{ mt: 1, display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
                {canWrite && (
                  <Button variant="outlined" color="error" sx={{ borderWidth: 1.5, fontWeight: 600 }} disabled={selected.status !== 'REGISTERED' && selected.status !== 'INACTIVE'} onClick={() => { activate.reset(); setConfirm('activate'); }} data-testid="set-active">
                    Set as active
                  </Button>
                )}
                <Button variant="outlined" onClick={() => void download()} disabled={!selected.sha256}>
                  Download
                </Button>
                <Button variant="outlined" onClick={() => void exportLabels()} disabled={selected.labelCount === 0} sx={selected.labelCount === 0 ? { borderStyle: 'dashed' } : undefined}>
                  Export labels
                </Button>
              </Box>
              <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
                POST .../versions/{selected.version}/activate {'·'} GET .../download (302 presigned, 15 min) {'·'} GET /models/{'{id}'}/feedback/export?versionId{selected.labelCount === 0 ? ' (disabled: no labels)' : ''}
              </Typography>
            </>
          ) : (
            <Typography variant="caption">Select a version in the table.</Typography>
          )}
        </Card>
      </Box>

      <ConfirmDialog
        open={confirm === 'activate'}
        title={`Activate version ${selected?.version ?? ''}?`}
        confirmLabel="Activate"
        busy={activate.isPending}
        disabled={br10Fails && !(force && note.trim())}
        onCancel={() => setConfirm(null)}
        onConfirm={() => selected && activate.mutate({ version: selected.version, forceIt: force, noteText: note })}
      >
        <Box sx={{ fontSize: 12.5, lineHeight: 1.7, '& b': { display: 'inline-block', width: 92, color: '#444', fontWeight: 400 } }}>
          <b>Active now</b> {active ? `${active.version} (macro-F1 ${active.metrics?.macroF1.toFixed(2) ?? '-'}) → becomes INACTIVE` : 'none'}
          <br />
          <b>Target</b> {selected?.version} (macro-F1 {selected?.metrics?.macroF1.toFixed(2) ?? '-'}), {br10Fails ? 'BR-10 fails: more than 0.05 below the active version' : 'BR-10 passes'}
        </Box>
        <FormControlLabel sx={{ '& .MuiFormControlLabel-label': { fontSize: 12.5 } }} control={<Checkbox size="small" checked={force} onChange={(event) => setForce(event.target.checked)} />} label="Force (needed only when the rule fails; requires a note)" />
        <TextField label="Note" value={note} onChange={(event) => setNote(event.target.value)} fullWidth margin="dense" />
        {activateError && (
          <Alert severity="error" sx={{ fontSize: 12, py: 0.25, mt: 0.5 }} data-testid="activate-error">
            {errorMessage(activateError)}
            {typeof activateDetails.activeMacroF1 === 'number' && typeof activateDetails.targetMacroF1 === 'number' ? ` (active ${activateDetails.activeMacroF1.toFixed(2)}, target ${activateDetails.targetMacroF1.toFixed(2)}; force with a note to activate anyway)` : ''}
          </Alert>
        )}
        <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
          The C4 inference worker reloads within a few seconds (model.activated); audited as MODEL_ACTIVATE.
        </Typography>
      </ConfirmDialog>

      <ConfirmDialog open={confirm === 'rollback'} title={`Roll back ${model?.name ?? ''}?`} confirmLabel="Roll back" busy={rollback.isPending} onCancel={() => setConfirm(null)} onConfirm={() => rollback.mutate(note)}>
        The previously active version becomes ACTIVE again and {active ? `${active.version} becomes INACTIVE` : 'the current version is deactivated'}. POST /models/{'{id}'}/rollback.
        <TextField label="Note" value={note} onChange={(event) => setNote(event.target.value)} fullWidth margin="dense" />
        {rollback.error ? <ErrorNote error={rollback.error} /> : null}
      </ConfirmDialog>

      {model && (
        <UploadVersionDialog
          open={uploadOpen}
          modelId={model.modelId}
          modelName={model.name}
          suggestedVersion={nextVersion(versionRows)}
          onClose={() => setUploadOpen(false)}
          onUploaded={(version) => {
            toast(`Version ${version.version} registered`);
            setUploadOpen(false);
            // Put the new row into the cached list first: otherwise the selection effect sees an
            // id that is not in the (not yet refetched) list and falls back to another version.
            queryCache.setQueryData<VersionDto[]>(queryKeys.versions(model.modelId), (rows) => [version, ...(rows ?? []).filter((r) => r.versionId !== version.versionId)]);
            setVersionId(version.versionId);
            refresh();
          }}
        />
      )}

      <Dialog open={newModelOpen} onClose={() => setNewModelOpen(false)} maxWidth="xs" fullWidth>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            createModel.mutate(newModel);
          }}
        >
          <DialogTitle>New model</DialogTitle>
          <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.25, pt: '8px !important' }}>
            <TextField label="Name" value={newModel.name} onChange={(event) => setNewModel({ ...newModel, name: event.target.value })} required autoFocus />
            <TextField label="Task" value={newModel.task} onChange={(event) => setNewModel({ ...newModel, task: event.target.value })} required placeholder="audio event classification (CLAP + head)" />
            {createModel.error ? <ErrorNote error={createModel.error} /> : null}
          </DialogContent>
          <DialogActions sx={{ px: 2, pb: 1.5 }}>
            <Button variant="outlined" onClick={() => setNewModelOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="contained" disabled={createModel.isPending || !newModel.name || !newModel.task}>
              Create
            </Button>
          </DialogActions>
        </form>
      </Dialog>
    </Box>
  );
}
