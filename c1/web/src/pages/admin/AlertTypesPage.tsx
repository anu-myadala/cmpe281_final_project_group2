// AlertTypesPage: the alert type table (8) with an edit panel (9). GET, PUT /admin/alert-types/{code}.
// Codes are fixed; C3 reads this table for the default severity and the escalation delay.
import { useEffect, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import TextField from '@mui/material/TextField';
import Select from '@mui/material/Select';
import MenuItem from '@mui/material/MenuItem';
import FormControl from '@mui/material/FormControl';
import InputLabel from '@mui/material/InputLabel';
import Typography from '@mui/material/Typography';
import { queryCache, useAlertTypes } from '../../api/queries';
import { ApiClient, isApiError } from '../../api/ApiClient';
import type { AlertTypeDto, Severity } from '../../api/types';
import { Card, DataTable, ErrorNote, Loading, type Column } from '../../components/common';
import { StatusChip } from '../../components/StatusChip';
import { useToast } from '../../components/Toast';
import { AdminTabs } from './AdminTabs';

const SEVERITIES: Severity[] = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'];

export function AlertTypesPage() {
  const { toast } = useToast();
  const types = useAlertTypes();
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [draft, setDraft] = useState<AlertTypeDto | null>(null);

  const rows = types.data ?? [];
  const selected = rows.find((t) => t.code === selectedCode) ?? null;
  useEffect(() => setDraft(selected ? { ...selected } : null), [selected]);

  const save = useMutation({
    mutationFn: (body: AlertTypeDto) =>
      ApiClient.put<AlertTypeDto>(`/admin/alert-types/${body.code}`, { name: body.name, defaultSeverity: body.defaultSeverity, escalationMinutes: body.escalationMinutes, description: body.description }),
    onSuccess: (data) => {
      toast(`Alert type ${data.code} saved`);
      void queryCache.invalidateQueries({ queryKey: ['admin', 'alert-types'] });
    },
  });
  const fieldError = isApiError(save.error) ? save.error.field : undefined;

  const columns: Column<AlertTypeDto>[] = [
    { key: 'code', header: 'Code', render: (t) => <b>{t.code}</b> },
    { key: 'name', header: 'Name' },
    { key: 'defaultSeverity', header: 'Default severity', render: (t) => <StatusChip value={t.defaultSeverity} /> },
    { key: 'escalationMinutes', header: 'Escalation (min)', align: 'right' },
    { key: 'description', header: 'Description', render: (t) => t.description ?? '' },
  ];

  return (
    <Box>
      <AdminTabs />
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 1.7fr) minmax(300px, 1fr)' }, gap: 1.25, alignItems: 'start' }}>
        <Card title="Alert types" note="GET, PUT /admin/alert-types/{code}" testId="alert-types-card">
          {types.isLoading ? <Loading /> : types.error ? <ErrorNote error={types.error} /> : <DataTable columns={columns} rows={rows} rowKey={(t) => t.code} selectedKey={selectedCode} onRowClick={(t) => setSelectedCode(t.code)} testId="alert-types-table" />}
          <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
            codes are fixed; C3 reads this table when it creates an alert and when it escalates one that stays NEW
          </Typography>
        </Card>
        <Card title={selected ? `Alert type: ${selected.code}` : 'Alert type'} testId="alert-type-panel">
          {draft ? (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25 }}>
              <TextField label="Name" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} error={fieldError === 'name'} helperText={fieldError === 'name' ? save.error?.message : undefined} />
              <FormControl>
                <InputLabel id="severity">Default severity</InputLabel>
                <Select labelId="severity" label="Default severity" value={draft.defaultSeverity} onChange={(event) => setDraft({ ...draft, defaultSeverity: event.target.value as Severity })}>
                  {SEVERITIES.map((s) => (
                    <MenuItem key={s} value={s}>
                      {s}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
              <TextField
                label="Escalation (minutes)"
                type="number"
                value={draft.escalationMinutes}
                onChange={(event) => setDraft({ ...draft, escalationMinutes: Number(event.target.value) })}
                inputProps={{ min: 0, max: 1440 }}
                error={fieldError === 'escalationMinutes'}
                helperText={fieldError === 'escalationMinutes' ? save.error?.message : 'minutes a NEW alert waits before C3 escalates it'}
              />
              <TextField label="Description" value={draft.description ?? ''} onChange={(event) => setDraft({ ...draft, description: event.target.value })} multiline minRows={2} />
              {save.error && !fieldError ? <ErrorNote error={save.error} /> : null}
              <Box>
                <Button variant="contained" disabled={save.isPending} onClick={() => save.mutate(draft)} data-testid="alert-type-save">
                  Save
                </Button>
              </Box>
            </Box>
          ) : (
            <Typography variant="caption">Select an alert type to edit its default severity and escalation delay.</Typography>
          )}
        </Card>
      </Box>
    </Box>
  );
}
