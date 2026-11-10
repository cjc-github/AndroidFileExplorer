/** Shared API types, mirroring the backend schemas. */

export const DeviceStatus = {
  online: "online",
  offline: "offline",
  unauthorized: "unauthorized",
  permissionDenied: "permission_denied",
  error: "error",
} as const;

export type DeviceStatus = (typeof DeviceStatus)[keyof typeof DeviceStatus];

export interface DeviceMetrics {
  cpu_percent: number | null;
  cpu_user_percent: number | null;
  cpu_system_percent: number | null;
  memory_total_bytes: number | null;
  memory_available_bytes: number | null;
  memory_used_bytes: number | null;
  memory_percent: number | null;
  load_average: number[] | null;
  sampled_at: string;
}

export interface Device {
  uid: string;
  serial: string;
  engine_id: number | null;
  transport_kind: string;
  alias: string | null;
  model: string | null;
  brand: string | null;
  cpu_abi: string | null;
  android_version: string | null;
  api_level: string | null;
  product: string | null;
  device: string | null;
  platform: string;
  status: DeviceStatus;
  is_rooted: boolean;
  is_network_device: boolean;
  last_error: string | null;
  last_seen: string | null;
  created_at: string;
  updated_at: string;
  metrics: DeviceMetrics | null;
}

export interface PlatformSettings {
  sync_interval_seconds: number;
  auto_root: boolean;
  auto_sync_seconds: number;
}

export interface Engine {
  id: number;
  name: string;
  host: string;
  port: number;
  kind: string;
  status: string;
  last_error: string | null;
  last_seen: string | null;
  created_at: string;
  updated_at: string;
}

export interface FileItem {
  name: string;
  path: string;
  is_dir: boolean;
  size: number | null;
  modified_at: string | null;
  permissions: string | null;
  owner: string | null;
  group: string | null;
  is_link: boolean;
  link_target: string | null;
}

export interface PathPart {
  name: string;
  path: string;
}

export interface FileList {
  current_path: string;
  parent_path: string | null;
  parts: PathPart[];
  items: FileItem[];
  truncated: boolean;
}

export interface TransferInfo {
  ok: boolean;
  path: string;
  size: number | null;
  stored_at: string | null;
  message: string;
}

/** The backend wraps every payload in {code, message, data, timestamp}. */
export interface ApiEnvelope<T> {
  code: number;
  message: string;
  data: T | null;
  timestamp: string;
}
