// Date and number formatting helpers shared by the pages.

export function parseIso(value: string | null | undefined): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatTime(value: string | null | undefined): string {
  const date = parseIso(value);
  if (!date) return '-';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function formatShortTime(value: string | null | undefined): string {
  const date = parseIso(value);
  if (!date) return '-';
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  if (sameDay) return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

export function formatDateTime(value: string | null | undefined): string {
  const date = parseIso(value);
  if (!date) return '-';
  return date.toLocaleString([], { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function formatDate(value: string | null | undefined): string {
  const date = parseIso(value);
  if (!date) return '-';
  return date.toISOString().slice(0, 10);
}

/** "4 s", "14 min", "3 h", "2 d" since the timestamp; "none" when missing. */
export function ago(value: string | null | undefined, now: number = Date.now()): string {
  const date = parseIso(value);
  if (!date) return 'none';
  const seconds = Math.max(0, Math.round((now - date.getTime()) / 1000));
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.round(hours / 24)} d`;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return '-';
  return value.toLocaleString([], { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return '-';
  return `${(value * 100).toFixed(1)} %`;
}

export function isoDay(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export function daysAgo(days: number): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() - days);
  return isoDay(date);
}

export function shortSha(sha: string | null | undefined): string {
  if (!sha) return '-';
  return `${sha.slice(0, 8)}...${sha.slice(-6)}`;
}
