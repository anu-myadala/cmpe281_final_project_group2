// useLiveEvents: the one WsClient of the app. Dispatches alert.created / alert.updated /
// device.status / model.activated to the QueryCache, keeps the ids of the alert rows to
// highlight for 3 s, and the open-alert badge count the shell shows.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useAuth } from '../auth/AuthProvider';
import { invalidateFor } from '../api/queries';
import type { AlertRow, LiveMessage, MapResponse } from '../api/types';
import { queryCache } from '../api/queries';
import { useToast } from '../components/Toast';
import { WsClient, type LiveHandler, type WsStatus } from './WsClient';

const HIGHLIGHT_MS = 3000;

export interface LiveState extends WsStatus {
  highlighted: Set<number>;
  /** Alerts received over alert.created since the page opened (newest first). */
  liveAlerts: AlertRow[];
  badgeDelta: number;
  badgePulse: number;
  resetBadgeDelta: () => void;
  subscribe: (type: string, handler: LiveHandler) => () => void;
}

const LiveContext = createContext<LiveState | null>(null);

export function LiveProvider({ children }: { children: ReactNode }) {
  const { accessToken, tenantId } = useAuth();
  const { toast } = useToast();
  const tokenRef = useRef<string | null>(accessToken);
  tokenRef.current = accessToken;
  // The tenant on screen: the user's own, or the one a staff user selected in the top bar.
  const tenantRef = useRef<number | null>(tenantId);
  tenantRef.current = tenantId;
  const clientRef = useRef<WsClient | null>(null);
  const [status, setStatus] = useState<WsStatus>({ connected: false, polling: false, missedPings: 0 });
  const [highlighted, setHighlighted] = useState<Set<number>>(new Set());
  const [liveAlerts, setLiveAlerts] = useState<AlertRow[]>([]);
  const [badgeDelta, setBadgeDelta] = useState(0);
  const [badgePulse, setBadgePulse] = useState(0);

  if (!clientRef.current) clientRef.current = new WsClient(() => tokenRef.current);

  const highlight = useCallback((alertId: number) => {
    setHighlighted((set) => new Set(set).add(alertId));
    window.setTimeout(() => {
      setHighlighted((set) => {
        const next = new Set(set);
        next.delete(alertId);
        return next;
      });
    }, HIGHLIGHT_MS);
  }, []);

  useEffect(() => {
    const client = clientRef.current!;
    const offStatus = client.onStatus(setStatus);
    const offAll = client.subscribe('*', (message: LiveMessage) => {
      invalidateFor(message.type);
      // Staff receive the events of every tenant, each with its tenantId. Rows, pins, the badge and
      // the toast follow only the tenant on screen; tenant users never get another tenant's events.
      const eventTenant = 'tenantId' in message && typeof message.tenantId === 'number' ? message.tenantId : null;
      const onScreen = eventTenant === null || eventTenant === tenantRef.current;
      if (message.type === 'alert.created' && 'alert' in message && message.alert) {
        if (!onScreen) return;
        const alert = message.alert as AlertRow & { lat?: number; lon?: number };
        highlight(alert.alertId);
        if (typeof alert.lat === 'number' && typeof alert.lon === 'number') {
          // A pin appears on every mounted map at once; the 10 s refetch confirms it from C3.
          queryCache.setQueriesData<MapResponse>({ queryKey: ['map', tenantRef.current] }, (current) =>
            current && !current.alerts.some((a) => a.alertId === alert.alertId)
              ? { ...current, alerts: [...current.alerts, { alertId: alert.alertId, vehicleId: alert.vehicleId, lat: alert.lat as number, lon: alert.lon as number, severity: alert.severity, type: alert.type, createdAt: alert.createdAt }] }
              : current,
          );
        }
        setLiveAlerts((rows) => [alert, ...rows.filter((r) => r.alertId !== alert.alertId)].slice(0, 50));
        setBadgeDelta((n) => n + 1);
        setBadgePulse((n) => n + 1);
        toast(`${alert.severity} ${alert.type} alert on ${alert.vehicleId}`, alert.severity === 'CRITICAL' ? 'error' : 'warning');
      } else if (message.type === 'alert.updated' && 'alertId' in message) {
        if (!onScreen) return;
        const status = String(message.status ?? '');
        const alertId = Number(message.alertId);
        if (status === 'RESOLVED' || status === 'FALSE_ALARM') {
          setBadgeDelta((n) => n - 1);
          queryCache.setQueriesData<MapResponse>({ queryKey: ['map'] }, (current) => (current ? { ...current, alerts: current.alerts.filter((a) => a.alertId !== alertId) } : current));
        }
        highlight(alertId);
      } else if (message.type === 'model.activated' && 'version' in message) {
        toast(`Version ${String(message.version)} is active`, 'info');
      }
    });
    return () => {
      offStatus();
      offAll();
    };
  }, [highlight, toast]);

  // Another tenant on screen (a staff user picked one): its live rows start empty.
  useEffect(() => {
    setLiveAlerts([]);
    setBadgeDelta(0);
  }, [tenantId]);

  useEffect(() => {
    const client = clientRef.current!;
    if (accessToken) client.start();
    else client.stop();
    return () => {
      client.stop();
    };
    // start once per session: the client re-reads the token on every (re)connect
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [Boolean(accessToken)]);

  const subscribe = useCallback((type: string, handler: LiveHandler) => clientRef.current!.subscribe(type, handler), []);
  const resetBadgeDelta = useCallback(() => setBadgeDelta(0), []);

  const value = useMemo<LiveState>(
    () => ({ ...status, highlighted, liveAlerts, badgeDelta, badgePulse, resetBadgeDelta, subscribe }),
    [status, highlighted, liveAlerts, badgeDelta, badgePulse, resetBadgeDelta, subscribe],
  );
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

export function useLiveEvents(): LiveState {
  const ctx = useContext(LiveContext);
  if (!ctx) throw new Error('useLiveEvents must be used inside LiveProvider');
  return ctx;
}

/** Subscribe a page handler to one message type while the page is mounted. */
export function useLiveHandler(type: string, handler: LiveHandler): void {
  const { subscribe } = useLiveEvents();
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => subscribe(type, (message) => ref.current(message)), [subscribe, type]);
}
