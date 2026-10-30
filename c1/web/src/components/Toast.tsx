// Toast: one message at the bottom right for 4 s after a save, export or activation.
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import Snackbar from '@mui/material/Snackbar';
import Alert from '@mui/material/Alert';

export type ToastSeverity = 'success' | 'info' | 'warning' | 'error';

interface ToastItem {
  id: number;
  message: string;
  severity: ToastSeverity;
}

interface ToastApi {
  toast: (message: string, severity?: ToastSeverity) => void;
}

const ToastContext = createContext<ToastApi>({ toast: () => undefined });

const TOAST_MS = 4000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [queue, setQueue] = useState<ToastItem[]>([]);
  const toast = useCallback((message: string, severity: ToastSeverity = 'success') => {
    setQueue((items) => [...items, { id: Date.now() + Math.random(), message, severity }]);
  }, []);
  const current = queue[0];
  const close = () => setQueue((items) => items.slice(1));
  const api = useMemo(() => ({ toast }), [toast]);
  return (
    <ToastContext.Provider value={api}>
      {children}
      <Snackbar
        key={current?.id}
        open={Boolean(current)}
        autoHideDuration={TOAST_MS}
        onClose={(_, reason) => {
          if (reason !== 'clickaway') close();
        }}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        {current ? (
          <Alert
            onClose={close}
            severity={current.severity}
            variant="filled"
            data-testid="toast"
            sx={{
              bgcolor: current.severity === 'success' || current.severity === 'info' ? '#1f2328' : undefined,
              color: '#fff',
              fontSize: 12.5,
              borderRadius: 1.5,
            }}
          >
            {current.message}
          </Alert>
        ) : undefined}
      </Snackbar>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  return useContext(ToastContext);
}
