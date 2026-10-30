// ChangePasswordDialog: forced after an invitation (mustChangePassword), or opened from the
// user menu. POST /auth/password/change; 400 field errors are shown inline.
import { useState, type FormEvent } from 'react';
import Dialog from '@mui/material/Dialog';
import DialogTitle from '@mui/material/DialogTitle';
import DialogContent from '@mui/material/DialogContent';
import DialogActions from '@mui/material/DialogActions';
import TextField from '@mui/material/TextField';
import Button from '@mui/material/Button';
import Typography from '@mui/material/Typography';
import Alert from '@mui/material/Alert';
import { ApiClient, errorMessage, isApiError } from '../api/ApiClient';
import { useAuth } from '../auth/AuthProvider';
import { useToast } from '../components/Toast';

export function ChangePasswordDialog({ open, forced, onClose }: { open: boolean; forced: boolean; onClose?: () => void }) {
  const { passwordChanged, logout, user } = useAuth();
  const { toast } = useToast();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reset = () => {
    setCurrent('');
    setNext('');
    setConfirm('');
    setError(null);
    setFieldError(null);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setFieldError(null);
    if (next.length < 10) {
      setFieldError('At least 10 characters');
      return;
    }
    if (user && next.toLowerCase() === user.email.toLowerCase()) {
      setFieldError('The password must differ from the email');
      return;
    }
    if (next !== confirm) {
      setFieldError('The two passwords differ');
      return;
    }
    setBusy(true);
    try {
      await ApiClient.post('/auth/password/change', { currentPassword: current, newPassword: next });
      toast('Password changed');
      passwordChanged();
      reset();
      onClose?.();
    } catch (err) {
      if (isApiError(err) && err.field === 'newPassword') setFieldError(err.message);
      else setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={forced ? undefined : onClose} maxWidth="xs" fullWidth disableEscapeKeyDown={forced}>
      <form onSubmit={submit}>
        <DialogTitle>{forced ? 'Set a new password' : 'Change password'}</DialogTitle>
        <DialogContent>
          {forced && (
            <Typography sx={{ fontSize: 12, color: '#555', mb: 1 }}>
              You signed in with a temporary password. Choose a new one before continuing (at least 10 characters, not your email).
            </Typography>
          )}
          <TextField label="Current password" type="password" fullWidth margin="dense" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" inputProps={{ 'data-testid': 'pw-current' }} />
          <TextField
            label="New password"
            type="password"
            fullWidth
            margin="dense"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            error={Boolean(fieldError)}
            helperText={fieldError ?? 'Minimum 10 characters, not the email'}
            autoComplete="new-password"
            inputProps={{ 'data-testid': 'pw-new' }}
          />
          <TextField label="Repeat new password" type="password" fullWidth margin="dense" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" inputProps={{ 'data-testid': 'pw-confirm' }} />
          {error && (
            <Alert severity="error" sx={{ mt: 1, fontSize: 12, py: 0.25 }}>
              {error}
            </Alert>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 2, pb: 1.5 }}>
          {forced ? (
            <Button variant="outlined" onClick={() => void logout()}>
              Sign out
            </Button>
          ) : (
            <Button variant="outlined" onClick={onClose}>
              Cancel
            </Button>
          )}
          <Button type="submit" variant="contained" disabled={busy} data-testid="pw-submit">
            Change password
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}
