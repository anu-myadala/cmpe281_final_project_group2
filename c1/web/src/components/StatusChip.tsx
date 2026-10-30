// StatusChip: every chip carries its text and the palette of the style guide
// (severity, device connection, alert status, model version status, tenant / user status).
import Chip from '@mui/material/Chip';

type Kind = 'red' | 'orange' | 'yellow' | 'amber' | 'green' | 'blue' | 'gray' | 'strike';

const KIND_BY_TEXT: Record<string, Kind> = {
  CRITICAL: 'red',
  HIGH: 'orange',
  MEDIUM: 'yellow',
  LOW: 'gray',
  ONLINE: 'green',
  OFFLINE: 'amber',
  FAULT: 'red',
  PROVISIONING: 'gray',
  REGISTERED: 'gray',
  PROVISIONED: 'gray',
  NEVER: 'gray',
  DISABLED: 'strike',
  RETIRED: 'strike',
  NEW: 'blue',
  ACKNOWLEDGED: 'amber',
  RESOLVED: 'green',
  FALSE_ALARM: 'gray',
  ACTIVE: 'green',
  INACTIVE: 'gray',
  SUSPENDED: 'amber',
  LOCKED: 'amber',
  UPLOADING: 'amber',
  FAILED: 'red',
  OPEN: 'blue',
};

const STYLES: Record<Kind, { bg: string; fg: string; border: string; weight?: number; strike?: boolean }> = {
  red: { bg: '#fde2e1', fg: '#b42318', border: '#f2a7a2' },
  orange: { bg: '#ffe8cc', fg: '#b54708', border: '#f6c08a' },
  yellow: { bg: '#fff4c2', fg: '#7a5c00', border: '#e6cf6b' },
  amber: { bg: '#fff3cd', fg: '#8a6d00', border: '#e0c35a' },
  green: { bg: '#e3f5e9', fg: '#1e7b34', border: '#a6dcb4' },
  blue: { bg: '#dbeafe', fg: '#1d4ed8', border: '#93b4f5', weight: 700 },
  gray: { bg: '#eeedea', fg: '#555', border: '#c9c7c0' },
  strike: { bg: '#eeedea', fg: '#777', border: '#c9c7c0', strike: true },
};

export function chipKind(text: string | null | undefined): Kind {
  return KIND_BY_TEXT[String(text ?? '').toUpperCase()] ?? 'gray';
}

export function StatusChip({ value, label, size }: { value: string | null | undefined; label?: string; size?: 'small' }) {
  const text = value ?? 'NONE';
  const style = STYLES[chipKind(text)];
  return (
    <Chip
      label={label ?? text.replace('NONE', 'NO SAMPLE')}
      size={size ?? 'small'}
      data-status={text}
      sx={{
        bgcolor: style.bg,
        color: style.fg,
        border: `1px solid ${style.border}`,
        fontWeight: style.weight ?? 500,
        textDecoration: style.strike ? 'line-through' : 'none',
        height: 18,
        fontSize: 11,
      }}
    />
  );
}
