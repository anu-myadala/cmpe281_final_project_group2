// Wire types of the C1 API (camelCase JSON, ISO-8601 UTC timestamps) and of the
// C2/C3 calls the browser makes through the configurable base paths.

export type Role = 'OWNER' | 'FLEET_MANAGER' | 'RIDER' | 'IOT_ENGINEER' | 'SERVICE_STAFF' | 'SYS_ADMIN';
export type Plan = 'BASIC' | 'FLEET' | 'ENTERPRISE';
export type Severity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
export type AlertStatus = 'NEW' | 'ACKNOWLEDGED' | 'RESOLVED' | 'FALSE_ALARM';
export type Channel = 'DASHBOARD' | 'EMAIL' | 'SMS' | 'PUSH';
export type Connection = 'ONLINE' | 'OFFLINE' | 'NEVER' | null;

export interface ApiErrorBody {
  error: { code: string; message: string; details?: Record<string, unknown> };
}

export interface User {
  userId: number;
  email: string;
  name: string;
  role: Role;
  tenantId: number | null;
  tenantName?: string | null;
  plan: Plan | null;
  mustChangePassword: boolean;
}

export interface LoginResponse {
  accessToken: string;
  expiresIn: number;
  mustChangePassword?: boolean;
  user: User;
}

export interface MeResponse {
  user: User;
  permissions: string[];
}

export interface DeviceSummary {
  total: number;
  online: number;
  offline: number;
  fault: number;
  disabled: number;
}

export interface VehicleCardDto {
  vehicleId: string;
  vin: string | null;
  label: string | null;
  make?: string | null;
  model?: string | null;
  year?: number | null;
  status: string;
  connection: Connection;
  lastSeenAt: string | null;
  deviceSummary: DeviceSummary | null;
  lastEventAt?: string | null;
  openAlerts?: number;
  topClass24h?: string | null;
}

export interface AlertRow {
  alertId: number;
  vehicleId: string;
  type: string;
  severity: Severity;
  status: AlertStatus;
  topClass: string | null;
  confidence: number | null;
  createdAt: string;
  ageSeconds?: number;
  acknowledgedBy?: string | null;
}

export interface Overview {
  tenant: { tenantId: number; name: string; plan: Plan };
  kpis: { vehicles: number; online: number; openAlerts: number; alerts24h: number; events24h: number };
  vehicles: VehicleCardDto[];
  recentAlerts: AlertRow[];
  asOf: string;
  degraded: boolean;
}

export interface MapVehicle {
  vehicleId: string;
  label: string | null;
  status: string;
  connection: 'ONLINE' | 'OFFLINE' | null;
  lat: number | null;
  lon: number | null;
  headingDeg: number | null;
  speedKph: number | null;
  driveMode: string | null;
  ignition: boolean | null;
  sampleAt: string | null;
}

export interface MapAlert {
  alertId: number;
  vehicleId: string;
  lat: number;
  lon: number;
  severity: Severity;
  type: string;
  createdAt: string;
}

export interface MapResponse {
  vehicles: MapVehicle[];
  alerts: MapAlert[];
  asOf: string;
  degraded?: boolean;
}

export interface TelemetrySample {
  ts: string;
  speedKph?: number | null;
  driveMode?: string | null;
  ignition?: boolean | null;
  location?: { type: 'Point'; coordinates: [number, number] } | null;
  lat?: number | null;
  lon?: number | null;
}

export interface Stats {
  granularity: 'hour' | 'day';
  from: string;
  to: string;
  alertsByTypePerPeriod: Array<{ period: string } & Record<string, number | string>>;
  eventsByClass: Array<{ class: string; count: number }>;
  deviceStatus: Record<string, number | boolean>;
  falseAlarmRate: number;
  meanAckSeconds: number | null;
  totals: { alerts: number; open: number; events: number };
}

export interface ServiceSubscription {
  serviceCode: string;
  name: string;
  available: boolean;
  enabled: boolean;
  channels: Channel[];
  quietFrom: string | null;
  quietTo: string | null;
}

export interface SubscriptionDto {
  vehicleId: string;
  label: string | null;
  services: ServiceSubscription[];
  notifyRiders: boolean;
  updatedAt: string | null;
}

export interface SubscriptionBody {
  services: Array<{ serviceCode: string; enabled: boolean; channels: Channel[]; quietFrom?: string | null; quietTo?: string | null }>;
  notifyRiders: boolean;
}

export interface IntelligenceService {
  serviceCode: string;
  name: string;
  available: boolean;
  description: string | null;
}

export interface ReportResult {
  type: string;
  columns: string[];
  rows: Array<Array<string | number | null>>;
  total: number;
  from: string;
  to: string;
}

export interface Paged<T> {
  items: T[];
  page: number;
  size: number;
  total: number;
}

export interface TenantDto {
  tenantId: number;
  code: string;
  name: string;
  tenantType: 'OWNER' | 'FLEET' | 'SERVICE_PROVIDER';
  plan: Plan;
  maxVehicles: number;
  timeZone: string;
  status: 'ACTIVE' | 'SUSPENDED';
  vehicles: number;
  users: number;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface AdminUserDto {
  userId: number;
  email: string;
  name: string;
  role: Role;
  tenantId: number | null;
  status: 'ACTIVE' | 'DISABLED' | 'LOCKED';
  mustChangePassword: boolean;
  lastLoginAt: string | null;
  createdAt: string | null;
}

export interface AlertTypeDto {
  code: string;
  name: string;
  defaultSeverity: Severity;
  escalationMinutes: number;
  description: string | null;
}

export interface ModelDto {
  modelId: number;
  name: string;
  task: string;
  activeVersion: string | null;
  versions: number;
  createdAt: string | null;
}

export interface PerClassMetric {
  precision: number;
  recall: number;
  f1: number;
  support?: number;
}

export interface VersionMetrics {
  macroF1: number;
  accuracy: number;
  perClass: Record<string, PerClassMetric>;
}

export type VersionStatus = 'UPLOADING' | 'REGISTERED' | 'ACTIVE' | 'INACTIVE' | 'FAILED';

export interface VersionDto {
  versionId: number;
  modelId: number;
  version: string;
  status: VersionStatus;
  metrics: VersionMetrics | null;
  artifactUri: string | null;
  sha256: string | null;
  sizeBytes: number | null;
  createdBy: number | null;
  createdAt: string | null;
  activatedAt: string | null;
  deactivatedAt: string | null;
  notes: string | null;
  labelCount: number;
}

export interface BeginUploadResponse {
  versionId: number;
  version: string;
  status: VersionStatus;
  upload: { url: string; key: string; expiresInSeconds: number };
}

// GET /models/{id}/deployment: what each C4 classifier worker runs
export type WorkerState = 'READY' | 'LOADING' | 'FAILED';

export interface DeploymentWorker {
  workerId: string;
  modelVersion: string | null;
  state: WorkerState;
  loadedAt: string | null;
  sha256: string | null;
  sha256Verified: boolean;
  lastHeartbeatAt: string | null;
  detail: string | null;
  stale?: boolean;
  runsActiveVersion?: boolean;
}

export interface DeploymentResponse {
  modelId: number;
  activeVersion: string | null;
  workers: DeploymentWorker[];
  asOf: string;
}

// C2 vehicle list (through the C2 base path)
export interface C2Vehicle {
  vehicleId: string;
  vin: string | null;
  label: string | null;
  make: string | null;
  model: string | null;
  year: number | null;
  status: string;
  connection: Connection;
  lastSeenAt: string | null;
  retentionDays?: number | null;
  deviceSummary: DeviceSummary | null;
}

export interface C2Device {
  deviceId: string;
  type: string;
  status: string;
  lastSeenAt: string | null;
}

// WebSocket messages
export type LiveMessage =
  | { type: 'ping' }
  | { type: 'alert.created'; alert: AlertRow & { lat?: number; lon?: number } }
  | { type: 'alert.updated'; alertId: number; status: AlertStatus; by?: string }
  | { type: 'device.status'; vehicleId: string; deviceId?: string; status: string; lastSeenAt?: string; faultReason?: string; muted?: boolean }
  | { type: 'model.activated'; modelId: number; version: string }
  | ({ type: 'model.deployed'; modelId: number } & DeploymentWorker)
  | { type: string; [key: string]: unknown };
