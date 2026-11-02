// QueryCache: the TanStack Query keys and hooks for the GET responses. Overview and map are
// stale after 10 s (the map refetches every 10 s), lists after 60 s; live messages invalidate.
import { QueryClient, useQuery, type UseQueryOptions } from '@tanstack/react-query';
import { ApiClient, c2, c3 } from './ApiClient';
import type {
  AdminUserDto,
  AlertRow,
  AlertTypeDto,
  C2Device,
  C2Vehicle,
  DeploymentResponse,
  IntelligenceService,
  MapResponse,
  ModelDto,
  Overview,
  Paged,
  ReportResult,
  Stats,
  SubscriptionDto,
  TelemetrySample,
  TenantDto,
  VersionDto,
} from './types';

export const STALE_LIVE_MS = 10_000;
export const STALE_LIST_MS = 60_000;

export const queryCache = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: STALE_LIST_MS,
      retry: (failureCount, error) => {
        const status = (error as { status?: number }).status;
        if (status && status >= 400 && status < 500) return false;
        return failureCount < 1;
      },
      refetchOnWindowFocus: false,
    },
  },
});

export const queryKeys = {
  overview: (tenantId: number | null) => ['overview', tenantId] as const,
  map: (tenantId: number | null) => ['map', tenantId] as const,
  trail: (vehicleId: string | null, tenantId: number | null) => ['trail', vehicleId, tenantId] as const,
  stats: (tenantId: number | null, from: string, to: string, granularity: string) => ['stats', tenantId, from, to, granularity] as const,
  subscriptions: (tenantId: number | null) => ['subscriptions', tenantId] as const,
  services: ['intelligence-services'] as const,
  vehicles: (tenantId: number | null) => ['c2', 'vehicles', tenantId] as const,
  devices: (vehicleId: string, tenantId: number | null) => ['c2', 'devices', vehicleId, tenantId] as const,
  alerts: (tenantId: number | null, status: string) => ['c3', 'alerts', tenantId, status] as const,
  tenants: (params: Record<string, unknown>) => ['admin', 'tenants', params] as const,
  users: (params: Record<string, unknown>) => ['admin', 'users', params] as const,
  alertTypes: ['admin', 'alert-types'] as const,
  models: ['models'] as const,
  versions: (modelId: number | null) => ['models', modelId, 'versions'] as const,
  deployment: (modelId: number | null) => ['models', modelId, 'deployment'] as const,
  report: (type: string, params: Record<string, unknown>) => ['report', type, params] as const,
};

/** Invalidate the queries a live message touches. */
export function invalidateFor(type: string): void {
  switch (type) {
    case 'alert.created':
    case 'alert.updated':
      void queryCache.invalidateQueries({ queryKey: ['overview'] });
      void queryCache.invalidateQueries({ queryKey: ['map'] });
      void queryCache.invalidateQueries({ queryKey: ['c3'] });
      void queryCache.invalidateQueries({ queryKey: ['stats'] });
      break;
    case 'device.status':
      void queryCache.invalidateQueries({ queryKey: ['overview'] });
      void queryCache.invalidateQueries({ queryKey: ['map'] });
      void queryCache.invalidateQueries({ queryKey: ['c2'] });
      break;
    case 'model.activated':
      void queryCache.invalidateQueries({ queryKey: ['models'] });
      break;
    case 'model.deployed':
      // Only the deployment card changes; the versions table stays as it is.
      void queryCache.invalidateQueries({ predicate: (query) => query.queryKey[0] === 'models' && query.queryKey[2] === 'deployment' });
      break;
    default:
      break;
  }
}

type Opts<T> = Omit<UseQueryOptions<T>, 'queryKey' | 'queryFn'>;

export function useOverview(tenantId: number | null, polling: boolean, opts?: Opts<Overview>) {
  return useQuery<Overview>({
    queryKey: queryKeys.overview(tenantId),
    queryFn: () => ApiClient.get<Overview>('/dashboard/overview', { tenantId }),
    staleTime: STALE_LIVE_MS,
    refetchInterval: polling ? STALE_LIVE_MS : 30_000,
    enabled: tenantId !== null,
    ...opts,
  });
}

export function useVehicleMap(tenantId: number | null, opts?: Opts<MapResponse>) {
  return useQuery<MapResponse>({
    queryKey: queryKeys.map(tenantId),
    queryFn: () => ApiClient.get<MapResponse>('/dashboard/map', { tenantId }),
    staleTime: STALE_LIVE_MS,
    refetchInterval: STALE_LIVE_MS,
    enabled: tenantId !== null,
    ...opts,
  });
}

export function useTrail(vehicleId: string | null, tenantId: number | null) {
  return useQuery<TelemetrySample[]>({
    queryKey: queryKeys.trail(vehicleId, tenantId),
    queryFn: async () => {
      const data = await c2.get<TelemetrySample[] | { items: TelemetrySample[] }>(`/vehicles/${vehicleId}/telemetry`, {
        limit: 60,
        tenantId,
      });
      return Array.isArray(data) ? data : data.items ?? [];
    },
    staleTime: STALE_LIVE_MS,
    refetchInterval: STALE_LIVE_MS,
    enabled: Boolean(vehicleId),
    retry: false,
  });
}

export function useStats(tenantId: number | null, from: string, to: string, granularity: string, enabled = true) {
  return useQuery<Stats>({
    queryKey: queryKeys.stats(tenantId, from, to, granularity),
    queryFn: () => ApiClient.get<Stats>('/dashboard/stats', { tenantId, from, to, granularity }),
    enabled: enabled && tenantId !== null,
    staleTime: STALE_LIVE_MS,
  });
}

export function useSubscriptions(tenantId: number | null, vehicleId?: string) {
  return useQuery<SubscriptionDto[]>({
    queryKey: [...queryKeys.subscriptions(tenantId), vehicleId ?? 'all'],
    queryFn: () => ApiClient.get<SubscriptionDto[]>('/subscriptions', { tenantId, vehicleId }),
    enabled: tenantId !== null,
  });
}

export function useIntelligenceServices() {
  return useQuery<IntelligenceService[]>({
    queryKey: queryKeys.services,
    queryFn: () => ApiClient.get<IntelligenceService[]>('/intelligence-services'),
    staleTime: 5 * 60_000,
  });
}

export function useC2Vehicles(tenantId: number | null) {
  return useQuery<Paged<C2Vehicle>>({
    queryKey: queryKeys.vehicles(tenantId),
    queryFn: async () => {
      const data = await c2.get<Paged<C2Vehicle> | C2Vehicle[]>('/vehicles', { tenantId, size: 100 });
      return Array.isArray(data) ? { items: data, page: 1, size: data.length, total: data.length } : data;
    },
    enabled: tenantId !== null,
    retry: false,
  });
}

export function useC2Devices(vehicleId: string | null, tenantId: number | null) {
  return useQuery<C2Device[]>({
    queryKey: queryKeys.devices(vehicleId ?? '', tenantId),
    queryFn: async () => {
      const data = await c2.get<C2Device[] | { items: C2Device[] }>(`/vehicles/${vehicleId}/devices`, { tenantId });
      return Array.isArray(data) ? data : data.items ?? [];
    },
    enabled: Boolean(vehicleId),
    retry: false,
  });
}

export function useC3Alerts(tenantId: number | null, status: string, polling: boolean) {
  return useQuery<{ items: AlertRow[]; total: number }>({
    queryKey: queryKeys.alerts(tenantId, status),
    queryFn: async () => {
      const data = await c3.get<{ items: AlertRow[]; total: number } | AlertRow[]>('/alerts', {
        tenantId,
        status: status === 'ALL' ? undefined : status,
        size: 100,
        sort: 'createdAt',
      });
      return Array.isArray(data) ? { items: data, total: data.length } : data;
    },
    enabled: tenantId !== null,
    staleTime: STALE_LIVE_MS,
    refetchInterval: polling ? STALE_LIVE_MS : false,
    retry: false,
  });
}

export function useTenants(params: { status?: string; q?: string; page?: number; size?: number } = {}, enabled = true) {
  return useQuery<Paged<TenantDto>>({
    queryKey: queryKeys.tenants(params),
    queryFn: () => ApiClient.get<Paged<TenantDto>>('/admin/tenants', { size: 100, ...params }),
    enabled,
  });
}

export function useUsers(params: { tenantId?: number | null; role?: string; status?: string; q?: string; page?: number; size?: number } = {}) {
  return useQuery<Paged<AdminUserDto>>({
    queryKey: queryKeys.users(params),
    queryFn: () => ApiClient.get<Paged<AdminUserDto>>('/admin/users', { size: 100, ...params }),
  });
}

export function useAlertTypes() {
  return useQuery<AlertTypeDto[]>({
    queryKey: queryKeys.alertTypes,
    queryFn: () => ApiClient.get<AlertTypeDto[]>('/admin/alert-types'),
  });
}

export function useModels() {
  return useQuery<ModelDto[]>({
    queryKey: queryKeys.models,
    queryFn: () => ApiClient.get<ModelDto[]>('/models'),
  });
}

export function useVersions(modelId: number | null) {
  return useQuery<VersionDto[]>({
    queryKey: queryKeys.versions(modelId),
    queryFn: () => ApiClient.get<VersionDto[]>(`/models/${modelId}/versions`),
    enabled: modelId !== null,
  });
}

/** Which version each classifier worker runs; refetched every 10 s besides model.deployed. */
export function useDeployment(modelId: number | null) {
  return useQuery<DeploymentResponse>({
    queryKey: queryKeys.deployment(modelId),
    queryFn: () => ApiClient.get<DeploymentResponse>(`/models/${modelId}/deployment`),
    enabled: modelId !== null,
    staleTime: STALE_LIVE_MS,
    refetchInterval: STALE_LIVE_MS,
  });
}

export function useReport(type: string, params: { from: string; to: string; vehicleId?: string; tenantId: number | null }, enabled: boolean) {
  return useQuery<ReportResult>({
    queryKey: queryKeys.report(type, params),
    queryFn: () => ApiClient.get<ReportResult>(`/reports/${type}`, { ...params, format: 'json' }),
    enabled: enabled && params.tenantId !== null,
  });
}
