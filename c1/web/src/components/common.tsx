// Small presentational pieces every page uses: Card, KpiTile, FilterBar, DataTable,
// ConfirmDialog, LiveBanner, Unavailable (the graceful C2/C3 "not available" state).
import { type ReactNode, useState } from 'react';
import Box from '@mui/material/Box';
import Paper from '@mui/material/Paper';
import Typography from '@mui/material/Typography';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import Button from '@mui/material/Button';
import Alert from '@mui/material/Alert';
import CircularProgress from '@mui/material/CircularProgress';
import { colors } from '../theme';
import { errorMessage, isApiError } from '../api/ApiClient';

export function Card({
  title,
  note,
  actions,
  children,
  sx,
  testId,
}: {
  title?: ReactNode;
  note?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  sx?: Record<string, unknown>;
  testId?: string;
}) {
  return (
    <Paper sx={{ p: '8px 10px', position: 'relative', minWidth: 0, ...sx }} data-testid={testId}>
      {(title || actions) && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.75, flexWrap: 'wrap' }}>
          <Typography variant="subtitle1" component="h3" sx={{ flex: 1, minWidth: 0 }}>
            {title}
            {note && (
              <Typography component="span" variant="caption" sx={{ ml: 0.75, fontWeight: 400 }}>
                {note}
              </Typography>
            )}
          </Typography>
          {actions}
        </Box>
      )}
      {children}
    </Paper>
  );
}

export function KpiTile({ label, value, hint, testId }: { label: string; value: ReactNode; hint?: string; testId?: string }) {
  return (
    <Paper sx={{ p: '6px 10px', flex: 1, minWidth: 110 }} data-testid={testId ?? 'kpi-tile'} data-kpi={label}>
      <Typography sx={{ fontSize: 24, fontWeight: 700, lineHeight: 1.1 }} data-testid="kpi-value">
        {value}
      </Typography>
      <Typography sx={{ fontSize: 12, color: colors.muted }}>
        {label}
        {hint && (
          <Typography component="span" variant="caption" sx={{ ml: 0.5 }}>
            {hint}
          </Typography>
        )}
      </Typography>
    </Paper>
  );
}

export function FilterBar({ children, trailing, sx }: { children: ReactNode; trailing?: ReactNode; sx?: Record<string, unknown> }) {
  return (
    <Paper sx={{ p: '6px 10px', mb: 1, display: 'flex', alignItems: 'flex-end', gap: 1.25, flexWrap: 'wrap', ...sx }} data-testid="filter-bar">
      {children}
      {trailing && <Box sx={{ ml: 'auto', display: 'flex', alignItems: 'center', gap: 1 }}>{trailing}</Box>}
    </Paper>
  );
}

export interface Column<T> {
  key: string;
  header: ReactNode;
  render?: (row: T) => ReactNode;
  align?: 'left' | 'right' | 'center';
  width?: number | string;
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  selectedKey,
  onRowClick,
  highlightedKeys,
  empty = 'No rows',
  maxHeight,
  dense,
  testId,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  selectedKey?: string | number | null;
  onRowClick?: (row: T) => void;
  highlightedKeys?: Set<string | number>;
  empty?: ReactNode;
  maxHeight?: number | string;
  dense?: boolean;
  testId?: string;
}) {
  return (
    <TableContainer sx={{ maxHeight, border: '1px solid #e3e0d8', borderRadius: 1 }} data-testid={testId}>
      <Table size="small" stickyHeader>
        <TableHead>
          <TableRow>
            {columns.map((col) => (
              <TableCell key={col.key} align={col.align} sx={{ width: col.width, py: dense ? 0.25 : undefined }}>
                {col.header}
              </TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.length === 0 && (
            <TableRow>
              <TableCell colSpan={columns.length} sx={{ color: colors.muted, textAlign: 'center', py: 2 }}>
                {empty}
              </TableCell>
            </TableRow>
          )}
          {rows.map((row) => {
            const key = rowKey(row);
            const selected = selectedKey !== undefined && selectedKey === key;
            const highlighted = highlightedKeys?.has(key) ?? false;
            return (
              <TableRow
                key={key}
                hover={Boolean(onRowClick)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                data-row-key={key}
                data-highlighted={highlighted ? 'true' : undefined}
                sx={{
                  cursor: onRowClick ? 'pointer' : 'default',
                  '& td': {
                    bgcolor: highlighted ? '#eef3fd' : selected ? colors.primaryLight : undefined,
                    fontWeight: selected ? 600 : undefined,
                    outline: highlighted ? `2px solid ${colors.primary}` : undefined,
                    outlineOffset: -2,
                    transition: 'background-color .6s ease',
                  },
                }}
              >
                {columns.map((col) => (
                  <TableCell key={col.key} align={col.align} sx={{ py: dense ? 0.25 : undefined }}>
                    {col.render ? col.render(row) : String((row as Record<string, unknown>)[col.key] ?? '')}
                  </TableCell>
                ))}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel = 'Confirm',
  destructive = true,
  busy,
  disabled,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  children?: ReactNode;
  confirmLabel?: string;
  destructive?: boolean;
  busy?: boolean;
  disabled?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog open={open} onClose={busy ? undefined : onCancel} maxWidth="xs" fullWidth aria-labelledby="confirm-title">
      <DialogTitle id="confirm-title">{title}</DialogTitle>
      <DialogContent>{children}</DialogContent>
      <DialogActions sx={{ px: 2, pb: 1.5 }}>
        <Button variant="outlined" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button
          variant={destructive ? 'outlined' : 'contained'}
          color={destructive ? 'error' : 'primary'}
          onClick={onConfirm}
          disabled={busy || disabled}
          sx={destructive ? { borderWidth: 1.5, fontWeight: 600 } : undefined}
          data-testid="confirm-button"
        >
          {busy ? <CircularProgress size={14} sx={{ mr: 1 }} /> : null}
          {confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export function LiveBanner({ polling }: { polling: boolean }) {
  if (!polling) return null;
  return (
    <Box
      role="status"
      data-testid="live-banner"
      sx={{
        bgcolor: colors.banner,
        border: `1px solid ${colors.bannerBorder}`,
        color: colors.bannerText,
        px: 1,
        py: 0.4,
        fontSize: 12,
        borderRadius: 1,
        mb: 1,
      }}
    >
      Live connection lost - showing data polled every 10 s
    </Box>
  );
}

/** Graceful state for a widget backed by C2 or C3 when that component is not reachable. */
export function Unavailable({ what, error, compact }: { what: string; error?: unknown; compact?: boolean }) {
  const detail = isApiError(error) ? `${error.status} ${error.code}` : error instanceof Error ? error.message : '';
  return (
    <Alert severity="warning" variant="outlined" sx={{ py: compact ? 0 : 0.5, fontSize: 12, borderStyle: 'dashed' }} data-testid="unavailable">
      {what} is not available right now{detail ? ` (${detail})` : ''}.
    </Alert>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <Alert severity="error" sx={{ fontSize: 12, py: 0.25 }} data-testid="error-note">
      {errorMessage(error)}
    </Alert>
  );
}

export function Loading({ label = 'Loading' }: { label?: string }) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, color: colors.muted, fontSize: 12, py: 1 }}>
      <CircularProgress size={14} /> {label}
    </Box>
  );
}

/** Generic hook for "open a confirm dialog with a payload". */
export function useConfirm<T>() {
  const [payload, setPayload] = useState<T | null>(null);
  return { payload, open: payload !== null, ask: (value: T) => setPayload(value), close: () => setPayload(null) };
}
