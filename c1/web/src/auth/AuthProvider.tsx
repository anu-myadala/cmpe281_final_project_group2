// AuthProvider / useAuth: access token in memory, refresh timer at 13 minutes, the
// mustChangePassword gate, and the tenant the staff roles are looking at.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ApiClient, bindTokenSource, isApiError } from '../api/ApiClient';
import type { LoginResponse, MeResponse, Role, User } from '../api/types';

export const STAFF_ROLES: Role[] = ['IOT_ENGINEER', 'SERVICE_STAFF', 'SYS_ADMIN'];
const REFRESH_AFTER_MS = 13 * 60 * 1000;
const TENANT_KEY = 'scicp.selectedTenantId';

export interface AuthState {
  user: User | null;
  permissions: string[];
  accessToken: string | null;
  mustChangePassword: boolean;
  ready: boolean;
  isStaff: boolean;
  /** Tenant the pages query: the user's own tenant, or the one the staff user selected. */
  tenantId: number | null;
  setSelectedTenant: (tenantId: number | null) => void;
  login: (email: string, password: string) => Promise<LoginResponse>;
  logout: () => Promise<void>;
  refresh: () => Promise<string | null>;
  passwordChanged: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

function readStoredTenant(): number | null {
  try {
    const raw = localStorage.getItem(TENANT_KEY);
    return raw ? Number(raw) : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [permissions, setPermissions] = useState<string[]>([]);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [mustChangePassword, setMustChangePassword] = useState(false);
  const [ready, setReady] = useState(false);
  const [selectedTenant, setSelectedTenantState] = useState<number | null>(readStoredTenant);
  const tokenRef = useRef<string | null>(null);
  const timerRef = useRef<number | null>(null);

  const setToken = useCallback((token: string | null) => {
    tokenRef.current = token;
    setAccessToken(token);
  }, []);

  const clearSession = useCallback(() => {
    setToken(null);
    setUser(null);
    setPermissions([]);
    setMustChangePassword(false);
    if (timerRef.current) window.clearTimeout(timerRef.current);
    timerRef.current = null;
  }, [setToken]);

  const refresh = useCallback(async (): Promise<string | null> => {
    try {
      const data = await ApiClient.post<{ accessToken: string; expiresIn: number }>('/auth/refresh', {}, { auth: false, retry: false });
      setToken(data.accessToken);
      return data.accessToken;
    } catch {
      return null;
    }
  }, [setToken]);

  const loadMe = useCallback(async () => {
    const me = await ApiClient.get<MeResponse>('/auth/me', undefined, { retry: false });
    setUser(me.user);
    setPermissions(me.permissions);
    setMustChangePassword(Boolean(me.user.mustChangePassword));
    return me;
  }, []);

  // ApiClient asks this provider for the token and for a refresh on 401.
  useEffect(() => {
    bindTokenSource({
      getToken: () => tokenRef.current,
      refresh,
      onAuthLost: clearSession,
    });
  }, [refresh, clearSession]);

  // Refresh timer: a new access token every 13 minutes while signed in.
  useEffect(() => {
    if (!accessToken) return;
    timerRef.current = window.setTimeout(() => {
      void refresh();
    }, REFRESH_AFTER_MS);
    return () => {
      if (timerRef.current) window.clearTimeout(timerRef.current);
    };
  }, [accessToken, refresh]);

  // On load: the httpOnly refresh cookie restores the session without a login.
  useEffect(() => {
    let canceled = false;
    (async () => {
      const token = await refresh();
      if (token && !canceled) {
        try {
          await loadMe();
        } catch {
          clearSession();
        }
      }
      if (!canceled) setReady(true);
    })();
    return () => {
      canceled = true;
    };
  }, [refresh, loadMe, clearSession]);

  const login = useCallback(
    async (email: string, password: string) => {
      const data = await ApiClient.post<LoginResponse>('/auth/login', { email, password }, { auth: false, retry: false });
      setToken(data.accessToken);
      setUser(data.user);
      setMustChangePassword(Boolean(data.user.mustChangePassword || data.mustChangePassword));
      try {
        await loadMe();
      } catch (error) {
        if (isApiError(error) && error.status === 401) clearSession();
        throw error;
      }
      return data;
    },
    [setToken, loadMe, clearSession],
  );

  const logout = useCallback(async () => {
    try {
      await ApiClient.post('/auth/logout', {}, { retry: false });
    } catch {
      // the session is gone either way
    }
    clearSession();
  }, [clearSession]);

  const setSelectedTenant = useCallback((tenantId: number | null) => {
    setSelectedTenantState(tenantId);
    try {
      if (tenantId === null) localStorage.removeItem(TENANT_KEY);
      else localStorage.setItem(TENANT_KEY, String(tenantId));
    } catch {
      // storage is a convenience only
    }
  }, []);

  const isStaff = user ? STAFF_ROLES.includes(user.role) : false;
  const tenantId = user ? (isStaff ? selectedTenant : user.tenantId) : null;

  const value = useMemo<AuthState>(
    () => ({
      user,
      permissions,
      accessToken,
      mustChangePassword,
      ready,
      isStaff,
      tenantId,
      setSelectedTenant,
      login,
      logout,
      refresh,
      passwordChanged: () => setMustChangePassword(false),
    }),
    [user, permissions, accessToken, mustChangePassword, ready, isStaff, tenantId, setSelectedTenant, login, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}
