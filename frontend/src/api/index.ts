import type { AxiosProgressEvent } from "axios";

import type {
  Device,
  DeviceMetrics,
  Engine,
  EnvironmentCheckResult,
  FileList,
  FilePreview,
  PlatformSettings,
  TransferInfo,
} from "./types";

import { request, unwrap } from "./request";

/** List every known device; `refresh` re-syncs the registry from all machines. */
export const getDeviceList = (refresh = false) =>
  unwrap<Device[]>(
    request.get("/devices", { params: refresh ? { refresh: "true" } : undefined }),
  );

/** One device with a fresh metric sample. */
export const getDeviceDetail = (uid: string) =>
  unwrap<Device>(request.get(`/devices/${uid}`));

/** One CPU / memory sample (used by the polling loop). */
export const getDeviceMetrics = (uid: string) =>
  unwrap<DeviceMetrics>(request.get(`/devices/${uid}/metrics`));

/** Reconnect a device (lifecycle operation bound to the unique id). */
export const connectDevice = (uid: string) =>
  unwrap<Device>(request.post(`/devices/${uid}/connect`));

/** Restart adbd with root privileges so protected paths become readable. */
export const tryRootDevice = (uid: string) =>
  unwrap<Device>(request.post(`/devices/${uid}/root`));

/** Restart adbd without root privileges again. */
export const unRootDevice = (uid: string) =>
  unwrap<Device>(request.post(`/devices/${uid}/unroot`));

/** Disconnect a device. */
export const disconnectDevice = (uid: string) =>
  unwrap<Device>(request.post(`/devices/${uid}/disconnect`));

/** List one directory of the device file system. */
export const getDeviceFileList = (uid: string, path: string) =>
  unwrap<FileList>(request.get(`/devices/${uid}/files`, { params: { path } }));

/** Search files by name below a path. */
export const searchDeviceFiles = (
  uid: string,
  keyword: string,
  path: string,
  limit = 200,
) =>
  unwrap<FileList>(
    request.get(`/devices/${uid}/files/search`, {
      params: { keyword, path, limit },
    }),
  );

/** Pull a device file through the machine that hosts it. */
export const pullDeviceFile = (uid: string, path: string) =>
  unwrap<TransferInfo>(request.post(`/devices/${uid}/files/pull`, { path }));

/** Create a directory on the device. */
export const mkdirOnDevice = (uid: string, path: string) =>
  unwrap<{ path: string }>(request.post(`/devices/${uid}/files/mkdir`, { path }));

/** Rename or move a file / directory on the device. */
export const renameOnDevice = (uid: string, source: string, target: string) =>
  unwrap<{ source: string; target: string }>(
    request.post(`/devices/${uid}/files/rename`, { source, target }),
  );

/** Small text / image preview of one device file. */
export const previewDeviceFile = (uid: string, path: string) =>
  unwrap<FilePreview>(
    request.get(`/devices/${uid}/files/preview`, { params: { path } }),
  );

/** Copy a file / directory on the device. */
export const copyOnDevice = (uid: string, source: string, target: string) =>
  unwrap<{ source: string; target: string }>(
    request.post(`/devices/${uid}/files/copy`, { source, target }),
  );

/** Change chmod / chown of a device path. */
export const changeDevicePermissions = (
  uid: string,
  path: string,
  payload: { mode?: string; owner?: string; group?: string },
) =>
  unwrap<{ path: string; mode: string | null; owner: string | null; group: string | null }>(
    request.post(`/devices/${uid}/files/permissions`, { path, ...payload }),
  );

/** Delete a file / directory on the device. */
export const removeOnDevice = (uid: string, path: string, recursive = false) =>
  unwrap<{ path: string }>(
    request.post(`/devices/${uid}/files/remove`, { path, recursive }),
  );

/** Read the platform settings. */
export const getSettings = () =>
  unwrap<PlatformSettings>(request.get("/settings"));

/** Replace the platform settings. */
export const updateSettings = (payload: PlatformSettings) =>
  unwrap<PlatformSettings>(request.put("/settings", payload));

/** Switch the root mode of several devices at once. */
export const batchRootDevices = (uids: string[], enable: boolean) =>
  unwrap<{ uid: string; ok: boolean; message: string; is_rooted: boolean | null }[]>(
    request.post("/devices/batch/root", { uids, enable }),
  );

/** Set the human readable name of a device. */
export const updateDeviceAlias = (uid: string, alias: string) =>
  unwrap<Device>(request.patch(`/devices/${uid}`, { alias }));

/** Upload a local file onto the device. */
export const uploadDeviceFile = (
  uid: string,
  path: string,
  file: File,
  options?: {
    signal?: AbortSignal;
    onUploadProgress?: (event: AxiosProgressEvent) => void;
  },
) => {
  const form = new FormData();
  form.append("file", file);
  return unwrap<TransferInfo>(
    request.post(`/devices/${uid}/files/upload`, form, {
      params: { device_path: path },
      // Let the browser set the multipart boundary.
      // A large upload must not be cut off by the 60s API default.
      timeout: 0,
      signal: options?.signal,
      onUploadProgress: options?.onUploadProgress,
    }),
  );
};

/** Zip a selection of device files / directories and return it as a blob. */
export const downloadSelectionArchive = (
  uid: string,
  paths: string[],
  options?: {
    signal?: AbortSignal;
    onDownloadProgress?: (event: AxiosProgressEvent) => void;
  },
) =>
  request.post<Blob>(
    `/devices/${uid}/files/archive-selection`,
    { paths },
    {
      responseType: "blob",
      timeout: 0,
      signal: options?.signal,
      onDownloadProgress: options?.onDownloadProgress,
    },
  );

/** Download a device file as a blob (used for progress / error feedback). */
export const downloadDeviceFile = (
  uid: string,
  path: string,
  options?: {
    signal?: AbortSignal;
    onDownloadProgress?: (event: AxiosProgressEvent) => void;
  },
) =>
  request.get<Blob>(`/devices/${uid}/files/download`, {
    params: { path },
    responseType: "blob",
    timeout: 0,
    signal: options?.signal,
    onDownloadProgress: options?.onDownloadProgress,
  });

/** Environment prerequisites (adb / asyncssh / Python / storage). */
export const getEnvironmentCheck = () =>
  unwrap<EnvironmentCheckResult>(request.get("/system/check"));

/** List the machines that host ADB servers. */
export const getEngineList = () => unwrap<Engine[]>(request.get("/engines"));

/** Register a remote machine. */
export const createEngine = (payload: {
  name: string;
  host: string;
  port: number;
  kind: "remote" | "ssh";
  token?: string;
  ssh_username?: string;
  ssh_password?: string;
  ssh_port?: number;
  adb_path?: string;
}) => unwrap<Engine>(request.post("/engines", payload));

/** Edit a registered machine (blank secret fields keep the stored value). */
export const updateEngine = (
  engineId: number,
  payload: {
    name?: string;
    host?: string;
    port?: number;
    token?: string;
    ssh_username?: string;
    ssh_password?: string;
    ssh_port?: number;
    adb_path?: string;
  },
) => unwrap<Engine>(request.patch(`/engines/${engineId}`, payload));

/** Probe one machine and report how many devices it sees. */
export const testEngine = (engineId: number) =>
  unwrap<{ engine_id: number; status: string; device_count: number }>(
    request.post(`/engines/${engineId}/test`),
  );

/** Remove a machine (and its device rows). */
export const deleteEngine = (engineId: number) =>
  unwrap<null>(request.delete(`/engines/${engineId}`));
