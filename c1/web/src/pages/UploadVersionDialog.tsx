// UploadVersionDialog: picks the artifact, computes its SHA-256 in the browser, registers the
// version (POST /models/{id}/versions), PUTs the file to the presigned URL, then calls complete
// with the checksum, size and held-out metrics.
import { useMemo, useState } from 'react';
import Dialog from '@mui/material/Dialog';
import DialogTitle from '@mui/material/DialogTitle';
import DialogContent from '@mui/material/DialogContent';
import DialogActions from '@mui/material/DialogActions';
import TextField from '@mui/material/TextField';
import Button from '@mui/material/Button';
import Typography from '@mui/material/Typography';
import Box from '@mui/material/Box';
import LinearProgress from '@mui/material/LinearProgress';
import Alert from '@mui/material/Alert';
import { ApiClient, errorMessage, isApiError } from '../api/ApiClient';
import type { BeginUploadResponse, PerClassMetric, VersionDto } from '../api/types';
import { formatBytes } from '../util/format';
import { sha256Hex } from '../util/sha256';

const CLASSES = ['siren', 'crash', 'glass_break', 'scream', 'honking', 'traffic', 'environmental'];
const SEMVER = /^\d+\.\d+\.\d+$/;

type Step = 'idle' | 'hashing' | 'registering' | 'uploading' | 'completing' | 'done';

function defaultPerClass(): Record<string, PerClassMetric> {
  return Object.fromEntries(CLASSES.map((cls) => [cls, { precision: 0.85, recall: 0.85, f1: 0.85, support: 60 }]));
}

export function UploadVersionDialog({ open, modelId, modelName, suggestedVersion, onClose, onUploaded }: { open: boolean; modelId: number; modelName: string; suggestedVersion: string; onClose: () => void; onUploaded: (version: VersionDto) => void }) {
  const [version, setVersion] = useState(suggestedVersion);
  const [notes, setNotes] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [macroF1, setMacroF1] = useState('0.87');
  const [accuracy, setAccuracy] = useState('0.90');
  const [perClass, setPerClass] = useState(() => JSON.stringify(defaultPerClass(), null, 2));
  const [step, setStep] = useState<Step>('idle');
  const [sha, setSha] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);

  const perClassParsed = useMemo(() => {
    try {
      const value = JSON.parse(perClass) as Record<string, PerClassMetric>;
      return typeof value === 'object' && value ? value : null;
    } catch {
      return null;
    }
  }, [perClass]);

  const busy = step !== 'idle' && step !== 'done';
  const valid = SEMVER.test(version) && file !== null && perClassParsed !== null && Number(macroF1) >= 0 && Number(macroF1) <= 1 && Number(accuracy) >= 0 && Number(accuracy) <= 1;

  const reset = () => {
    setStep('idle');
    setSha(null);
    setError(null);
    setFieldError(null);
  };

  const submit = async () => {
    if (!file || !perClassParsed) return;
    setError(null);
    setFieldError(null);
    try {
      setStep('hashing');
      const digest = await sha256Hex(file);
      setSha(digest);
      setStep('registering');
      const begin = await ApiClient.post<BeginUploadResponse>(`/models/${modelId}/versions`, { version, notes: notes || undefined, sizeBytes: file.size, sha256: digest });
      setStep('uploading');
      const put = await fetch(begin.upload.url, { method: 'PUT', body: file, headers: { 'Content-Type': 'application/octet-stream' } });
      if (!put.ok) throw new Error(`The storage answered ${put.status} to the artifact upload`);
      setStep('completing');
      const done = await ApiClient.post<VersionDto>(`/models/${modelId}/versions/${version}/complete`, {
        sha256: digest,
        sizeBytes: file.size,
        metrics: { macroF1: Number(macroF1), accuracy: Number(accuracy), perClass: perClassParsed },
      });
      setStep('done');
      onUploaded(done);
    } catch (err) {
      setStep('idle');
      if (isApiError(err) && err.field === 'version') setFieldError(err.message);
      else if (err instanceof TypeError) setError('The browser could not reach the storage URL (CORS or the storage is down). The version stays UPLOADING; complete it once the artifact is in the bucket.');
      else setError(errorMessage(err));
    }
  };

  const steps: Record<Step, string> = {
    idle: '',
    hashing: 'Computing SHA-256 in the browser...',
    registering: 'Registering the version (POST /models/{id}/versions)...',
    uploading: 'Uploading the artifact to the presigned URL...',
    completing: 'Verifying the checksum and saving the metrics (POST .../complete)...',
    done: 'Done',
  };

  return (
    <Dialog
      open={open}
      onClose={busy ? undefined : () => { reset(); onClose(); }}
      maxWidth="sm"
      fullWidth
    >
      <DialogTitle>Upload a new version of {modelName}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.25, pt: '8px !important' }}>
        <Box sx={{ display: 'flex', gap: 1.25 }}>
          <TextField label="Version (semver)" value={version} onChange={(event) => setVersion(event.target.value)} error={Boolean(fieldError) || (version !== '' && !SEMVER.test(version))} helperText={fieldError ?? (version && !SEMVER.test(version) ? 'MAJOR.MINOR.PATCH' : undefined)} sx={{ width: 180 }} disabled={busy} inputProps={{ 'data-testid': 'upload-version' }} />
          <TextField label="Notes" value={notes} onChange={(event) => setNotes(event.target.value)} fullWidth disabled={busy} />
        </Box>
        <Box>
          <Button variant="outlined" component="label" disabled={busy}>
            Choose artifact (model.pt)
            <input type="file" hidden onChange={(event) => setFile(event.target.files?.[0] ?? null)} data-testid="upload-file" />
          </Button>
          <Typography variant="caption" sx={{ ml: 1 }}>
            {file ? `${file.name} · ${formatBytes(file.size)}` : 'no file chosen'}
            {sha ? ` · sha256 ${sha.slice(0, 12)}...` : ''}
          </Typography>
        </Box>
        <Typography variant="subtitle2">Held-out test results</Typography>
        <Box sx={{ display: 'flex', gap: 1.25 }}>
          <TextField label="Macro-F1" type="number" value={macroF1} onChange={(event) => setMacroF1(event.target.value)} inputProps={{ step: 0.01, min: 0, max: 1, 'data-testid': 'upload-macro-f1' }} sx={{ width: 140 }} disabled={busy} />
          <TextField label="Accuracy" type="number" value={accuracy} onChange={(event) => setAccuracy(event.target.value)} inputProps={{ step: 0.01, min: 0, max: 1 }} sx={{ width: 140 }} disabled={busy} />
        </Box>
        <TextField label="Per-class metrics (JSON: class -> {precision, recall, f1, support})" value={perClass} onChange={(event) => setPerClass(event.target.value)} multiline minRows={4} maxRows={10} error={perClassParsed === null} helperText={perClassParsed === null ? 'Invalid JSON' : undefined} disabled={busy} inputProps={{ style: { fontFamily: 'monospace', fontSize: 11 } }} />
        {busy && (
          <Box>
            <LinearProgress />
            <Typography variant="caption">{steps[step]}</Typography>
          </Box>
        )}
        {error && (
          <Alert severity="error" sx={{ fontSize: 12, py: 0.25 }} data-testid="upload-error">
            {error}
          </Alert>
        )}
        <Typography variant="caption">
          The browser hashes the file, the API returns a presigned PUT URL (15 min), the file goes straight to the bucket, and complete verifies size and SHA-256 before the version becomes REGISTERED.
        </Typography>
      </DialogContent>
      <DialogActions sx={{ px: 2, pb: 1.5 }}>
        <Button variant="outlined" onClick={() => { reset(); onClose(); }} disabled={busy}>
          Cancel
        </Button>
        <Button variant="contained" onClick={() => void submit()} disabled={!valid || busy} data-testid="upload-submit">
          Upload and register
        </Button>
      </DialogActions>
    </Dialog>
  );
}
