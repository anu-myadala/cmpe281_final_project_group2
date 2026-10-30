// RecentAlertsTable: the last alerts with severity and status chips; Acknowledge posts
// the action to C3 (POST {C3 base}/alerts/{id}/actions). Rows added by alert.created are
// highlighted for 3 s.
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Button from '@mui/material/Button';
import Box from '@mui/material/Box';
import type { AlertRow } from '../api/types';
import { c3, errorMessage, isApiError } from '../api/ApiClient';
import { invalidateFor } from '../api/queries';
import { useToast } from './Toast';
import { StatusChip } from './StatusChip';
import { DataTable, type Column } from './common';
import { formatShortTime } from '../util/format';

export type AlertAction = 'ACKNOWLEDGE' | 'RESOLVE' | 'FALSE_ALARM';

export async function postAlertAction(alertId: number, action: AlertAction, tenantId: number | null, note?: string): Promise<void> {
  await c3.post(`/alerts/${alertId}/actions`, { action, note }, { tenantId });
  invalidateFor('alert.updated');
}

export function useAlertAction(tenantId: number | null) {
  const { toast } = useToast();
  const [busyId, setBusyId] = useState<number | null>(null);
  const run = async (alertId: number, action: AlertAction, note?: string) => {
    setBusyId(alertId);
    try {
      await postAlertAction(alertId, action, tenantId, note);
      const verb = action === 'ACKNOWLEDGE' ? 'acknowledged' : action === 'RESOLVE' ? 'resolved' : 'marked as false alarm';
      toast(`Alert ${alertId} ${verb}`);
      return true;
    } catch (error) {
      if (isApiError(error) && (error.status === 404 || error.status === 502 || error.status === 503 || error.status === 0)) {
        toast('The alert service (C3) is not available right now', 'warning');
      } else {
        toast(errorMessage(error), 'error');
      }
      return false;
    } finally {
      setBusyId(null);
    }
  };
  return { run, busyId };
}

export function RecentAlertsTable({
  rows,
  highlighted,
  tenantId,
  showVehicle = true,
  maxHeight,
  canAct = true,
}: {
  rows: AlertRow[];
  highlighted: Set<number>;
  tenantId: number | null;
  showVehicle?: boolean;
  maxHeight?: number;
  canAct?: boolean;
}) {
  const navigate = useNavigate();
  const { run, busyId } = useAlertAction(tenantId);
  const columns: Column<AlertRow>[] = [
    { key: 'time', header: 'Time', render: (r) => formatShortTime(r.createdAt) },
    ...(showVehicle ? [{ key: 'vehicleId', header: 'Vehicle' } as Column<AlertRow>] : []),
    { key: 'type', header: 'Type' },
    { key: 'severity', header: 'Sev.', render: (r) => <StatusChip value={r.severity} /> },
    { key: 'topClass', header: 'Class', render: (r) => r.topClass ?? '-' },
    { key: 'confidence', header: 'Conf.', render: (r) => (r.confidence !== null && r.confidence !== undefined ? r.confidence.toFixed(2) : '-') },
    { key: 'status', header: 'Status', render: (r) => <StatusChip value={r.status} /> },
    {
      key: 'action',
      header: 'Action',
      render: (r) => (
        <Box sx={{ display: 'flex', gap: 0.5 }}>
          {r.status === 'NEW' && canAct ? (
            <Button
              variant="contained"
              size="small"
              sx={{ py: 0.1, px: 1, fontSize: 11.5 }}
              disabled={busyId === r.alertId}
              onClick={(event) => {
                event.stopPropagation();
                void run(r.alertId, 'ACKNOWLEDGE');
              }}
              data-testid={`ack-${r.alertId}`}
            >
              Acknowledge
            </Button>
          ) : null}
          <Button
            variant="outlined"
            size="small"
            sx={{ py: 0.1, px: 1, fontSize: 11.5 }}
            onClick={(event) => {
              event.stopPropagation();
              navigate(`/alerts/${r.alertId}`);
            }}
          >
            Open
          </Button>
        </Box>
      ),
    },
  ];
  return <DataTable columns={columns} rows={rows} rowKey={(r) => r.alertId} highlightedKeys={highlighted} maxHeight={maxHeight} empty="No open alerts" testId="recent-alerts" />;
}
