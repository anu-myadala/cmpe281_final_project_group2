// WsClient: opens /ws, sends the auth message first, answers ping with pong, dispatches
// messages to subscribers, and falls back to polling after three missed pings or a
// closed socket (reconnect with exponential back-off).
import type { LiveMessage } from '../api/types';

export type LiveHandler = (message: LiveMessage) => void;

export interface WsStatus {
  connected: boolean;
  polling: boolean;
  missedPings: number;
}

const PING_INTERVAL_MS = 30_000;
const MAX_MISSED_PINGS = 3;
const MAX_BACKOFF_MS = 30_000;

function defaultUrl(): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${window.location.host}/ws`;
}

export class WsClient {
  private socket: WebSocket | null = null;
  private handlers = new Map<string, Set<LiveHandler>>();
  private statusListeners = new Set<(status: WsStatus) => void>();
  private tokenProvider: () => string | null;
  private url: string;
  private pingTimer: number | null = null;
  private reconnectTimer: number | null = null;
  private backoff = 1000;
  private stopped = true;
  private authFailed = false;
  status: WsStatus = { connected: false, polling: false, missedPings: 0 };

  constructor(tokenProvider: () => string | null, url: string = defaultUrl()) {
    this.tokenProvider = tokenProvider;
    this.url = url;
  }

  start(): void {
    this.stopped = false;
    this.authFailed = false;
    this.open();
  }

  stop(): void {
    this.stopped = true;
    this.clearTimers();
    if (this.socket) {
      const socket = this.socket;
      this.socket = null;
      socket.onclose = null;
      socket.close();
    }
    this.setStatus({ connected: false, polling: false, missedPings: 0 });
  }

  subscribe(type: string, handler: LiveHandler): () => void {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set());
    this.handlers.get(type)!.add(handler);
    return () => {
      this.handlers.get(type)?.delete(handler);
    };
  }

  onStatus(listener: (status: WsStatus) => void): () => void {
    this.statusListeners.add(listener);
    return () => {
      this.statusListeners.delete(listener);
    };
  }

  private setStatus(next: Partial<WsStatus>): void {
    this.status = { ...this.status, ...next };
    this.statusListeners.forEach((listener) => listener(this.status));
  }

  private clearTimers(): void {
    if (this.pingTimer) window.clearTimeout(this.pingTimer);
    if (this.reconnectTimer) window.clearTimeout(this.reconnectTimer);
    this.pingTimer = null;
    this.reconnectTimer = null;
  }

  private armPingWatch(): void {
    if (this.pingTimer) window.clearTimeout(this.pingTimer);
    // The server pings every 30 s; one missing ping is counted per interval (+ a little slack).
    this.pingTimer = window.setTimeout(() => {
      const missed = this.status.missedPings + 1;
      if (missed >= MAX_MISSED_PINGS) {
        this.setStatus({ missedPings: missed, polling: true, connected: false });
        this.socket?.close();
        return;
      }
      this.setStatus({ missedPings: missed });
      this.armPingWatch();
    }, PING_INTERVAL_MS + 5_000);
  }

  private open(): void {
    const token = this.tokenProvider();
    if (this.stopped || !token) return;
    let socket: WebSocket;
    try {
      socket = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;
    socket.onopen = () => {
      socket.send(JSON.stringify({ type: 'auth', token: this.tokenProvider() ?? token }));
      this.backoff = 1000;
      this.setStatus({ connected: true, polling: false, missedPings: 0 });
      this.armPingWatch();
    };
    socket.onmessage = (event: MessageEvent<string>) => {
      let message: LiveMessage;
      try {
        message = JSON.parse(event.data) as LiveMessage;
      } catch {
        return;
      }
      if (message.type === 'ping') {
        socket.send(JSON.stringify({ type: 'pong' }));
        this.setStatus({ missedPings: 0 });
        this.armPingWatch();
        return;
      }
      this.handlers.get(message.type)?.forEach((handler) => handler(message));
      this.handlers.get('*')?.forEach((handler) => handler(message));
    };
    socket.onclose = (event: CloseEvent) => {
      if (this.socket === socket) this.socket = null;
      if (event.code === 4401) this.authFailed = true;
      this.setStatus({ connected: false, polling: true });
      this.scheduleReconnect();
    };
    socket.onerror = () => {
      // onclose follows and schedules the reconnect
    };
  }

  private scheduleReconnect(): void {
    if (this.stopped) return;
    if (this.pingTimer) window.clearTimeout(this.pingTimer);
    this.pingTimer = null;
    if (this.reconnectTimer) return;
    const delay = this.backoff;
    this.backoff = Math.min(this.backoff * 2, MAX_BACKOFF_MS);
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      // After an auth failure wait for a fresh token (the provider is asked again on open).
      if (this.authFailed && !this.tokenProvider()) {
        this.scheduleReconnect();
        return;
      }
      this.authFailed = false;
      this.open();
    }, delay);
  }
}
