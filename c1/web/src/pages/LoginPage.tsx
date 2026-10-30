// LoginPage: centered form card without the shell, with the three error states
// (401 generic, 403 tenant suspended, 423 locked with a countdown; 429 "please wait").
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import Box from '@mui/material/Box';
import Paper from '@mui/material/Paper';
import Typography from '@mui/material/Typography';
import TextField from '@mui/material/TextField';
import Button from '@mui/material/Button';
import Alert from '@mui/material/Alert';
import { useAuth } from '../auth/AuthProvider';
import { isApiError } from '../api/ApiClient';
import { firstAllowedPath } from '../shell/menu';
import { colors } from '../theme';

type LoginError = { kind: '401' | '403' | '423' | '429' | 'other'; message: string; lockedUntil?: Date };

function describe(error: unknown): LoginError {
  if (isApiError(error)) {
    if (error.status === 401) return { kind: '401', message: 'Invalid email or password.' };
    if (error.status === 403) return { kind: '403', message: "Your organization's account is suspended. Contact SCICP support." };
    if (error.status === 423) {
      const raw = error.details.lockedUntil;
      const lockedUntil = typeof raw === 'string' ? new Date(raw) : new Date(Date.now() + 15 * 60_000);
      return { kind: '423', message: 'Too many failed attempts.', lockedUntil };
    }
    if (error.status === 429) return { kind: '429', message: 'Please wait a moment before trying again.' };
    return { kind: 'other', message: error.message };
  }
  return { kind: 'other', message: 'The server did not answer. Try again.' };
}

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<LoginError | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now());
  const passwordRef = useRef<HTMLInputElement>(null);

  const locked = error?.kind === '423' && error.lockedUntil !== undefined && error.lockedUntil.getTime() > now;
  useEffect(() => {
    if (!locked) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [locked]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || locked) return;
    setBusy(true);
    setError(null);
    try {
      const data = await login(email.trim(), password);
      const me = data.user;
      const from = (location.state as { from?: string } | null)?.from;
      navigate(from ?? firstAllowedPath(permissionsFor(me.role), me.role), { replace: true });
    } catch (err) {
      setError(describe(err));
      setPassword('');
      window.setTimeout(() => passwordRef.current?.focus(), 0);
    } finally {
      setBusy(false);
    }
  };

  const remaining = locked && error?.lockedUntil ? Math.max(0, Math.ceil((error.lockedUntil.getTime() - now) / 1000)) : 0;

  return (
    <Box sx={{ minHeight: '100vh', bgcolor: '#f2f2f0', display: 'flex', flexDirection: 'column' }}>
      <Box sx={{ height: 46, display: 'flex', alignItems: 'center', px: 2.25, fontWeight: 700, fontSize: 15 }}>
        <Box sx={{ width: 22, height: 22, borderRadius: 1, bgcolor: colors.primary, mr: 1 }} />
        SCICP Smart Car Intelligent Cloud Platform
      </Box>
      <Box sx={{ flex: 1, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', pt: { xs: 4, md: 10 }, px: 2 }}>
        <Paper component="form" onSubmit={submit} sx={{ width: 360, p: '18px 22px' }} noValidate data-testid="login-form">
          <Typography component="h2" sx={{ fontSize: 18, fontWeight: 700 }}>
            Sign in
          </Typography>
          <Typography sx={{ fontSize: 12, color: colors.muted, mb: 1.5 }}>One login for owners, fleet managers, riders and staff</Typography>
          <TextField
            label="Email"
            type="email"
            fullWidth
            margin="dense"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            autoComplete="username"
            autoFocus
            inputProps={{ 'data-testid': 'login-email' }}
          />
          <TextField
            label="Password"
            type="password"
            fullWidth
            margin="dense"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            inputRef={passwordRef}
            inputProps={{ 'data-testid': 'login-password' }}
          />
          {error && (
            <Alert severity="error" sx={{ mt: 1.25, fontSize: 12, py: 0.25 }} data-testid="login-error" data-kind={error.kind}>
              {error.message}
              {error.kind === '423' && error.lockedUntil && (
                <>
                  <br />
                  Account locked until {error.lockedUntil.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  {locked ? ` (${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')})` : ''}.
                </>
              )}
            </Alert>
          )}
          <Button type="submit" variant="contained" fullWidth sx={{ mt: 2, py: 1, fontSize: 13.5 }} disabled={busy || locked} data-testid="login-submit">
            {locked ? `Locked (${remaining} s)` : busy ? 'Signing in...' : 'Sign in'}
          </Button>
          <Typography sx={{ fontSize: 11.5, color: colors.muted, mt: 1.5, textAlign: 'center' }}>
            Forgot your password? Ask your administrator for a reset.
            <br />
            Invited users sign in with the temporary password from the email.
          </Typography>
        </Paper>
      </Box>
    </Box>
  );
}

// Until GET /auth/me answers, the role alone picks the first page (the router re-checks with the real list).
function permissionsFor(role: string): string[] {
  switch (role) {
    case 'RIDER':
      return ['alerts'];
    case 'IOT_ENGINEER':
      return ['vehicles', 'devices', 'provisioning', 'models', 'tenants'];
    default:
      return ['overview'];
  }
}
