// VersionTable and MetricsTable of the AI model pages (presentational).
import type { PerClassMetric, VersionDto } from '../api/types';
import { DataTable, type Column } from './common';
import { StatusChip } from './StatusChip';
import { formatBytes, formatDate } from '../util/format';

export function VersionTable({ versions, selectedId, onSelect }: { versions: VersionDto[]; selectedId: number | null; onSelect: (version: VersionDto) => void }) {
  const columns: Column<VersionDto>[] = [
    { key: 'version', header: 'Version', render: (v) => <b>{v.version}</b> },
    { key: 'status', header: 'Status', render: (v) => <StatusChip value={v.status} /> },
    { key: 'macroF1', header: 'Macro-F1', render: (v) => (v.metrics ? v.metrics.macroF1.toFixed(2) : '-') },
    { key: 'accuracy', header: 'Acc.', render: (v) => (v.metrics ? v.metrics.accuracy.toFixed(2) : '-') },
    { key: 'size', header: 'Size', render: (v) => formatBytes(v.sizeBytes) },
    { key: 'labels', header: 'Labels', render: (v) => v.labelCount },
    { key: 'created', header: 'Created', render: (v) => formatDate(v.createdAt) },
    { key: 'activated', header: 'Activated', render: (v) => formatDate(v.activatedAt) },
  ];
  const sorted = [...versions].sort((a, b) => compareSemver(b.version, a.version));
  return <DataTable columns={columns} rows={sorted} rowKey={(v) => v.versionId} selectedKey={selectedId} onRowClick={onSelect} empty="No versions yet" testId="version-table" />;
}

export function compareSemver(a: string, b: string): number {
  const pa = a.split('.').map(Number);
  const pb = b.split('.').map(Number);
  for (let i = 0; i < 3; i += 1) {
    const diff = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (diff !== 0) return diff;
  }
  return 0;
}

interface MetricRow {
  cls: string;
  precision: number | null;
  recall: number | null;
  f1: number | null;
  support: number | null;
  bold?: boolean;
}

export function MetricsTable({ perClass, macroF1, accuracy }: { perClass: Record<string, PerClassMetric>; macroF1: number; accuracy: number }) {
  const rows: MetricRow[] = Object.entries(perClass).map(([cls, m]) => ({ cls, precision: m.precision, recall: m.recall, f1: m.f1, support: m.support ?? null }));
  const macro = (key: 'precision' | 'recall') => (rows.length ? rows.reduce((sum, r) => sum + (r[key] ?? 0), 0) / rows.length : null);
  rows.push({ cls: 'macro', precision: macro('precision'), recall: macro('recall'), f1: macroF1, support: null, bold: true });
  rows.push({ cls: 'accuracy', precision: null, recall: null, f1: accuracy, support: null, bold: true });
  const fmt = (value: number | null) => (value === null ? '' : value.toFixed(2));
  const cell = (value: string, bold?: boolean) => (bold ? <b>{value}</b> : value);
  const columns: Column<MetricRow>[] = [
    { key: 'cls', header: 'Class', render: (r) => cell(r.cls, r.bold) },
    { key: 'precision', header: 'Precision', render: (r) => cell(fmt(r.precision), r.bold) },
    { key: 'recall', header: 'Recall', render: (r) => cell(fmt(r.recall), r.bold) },
    { key: 'f1', header: 'F1', render: (r) => cell(fmt(r.f1), r.bold) },
    { key: 'support', header: 'Support', render: (r) => (r.support === null ? '' : String(r.support)) },
  ];
  return <DataTable columns={columns} rows={rows} rowKey={(r) => r.cls} dense testId="metrics-table" />;
}
