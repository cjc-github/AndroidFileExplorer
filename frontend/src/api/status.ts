import type { BadgeProps } from "antd";

import type { DeviceStatus } from "./types";

export const deviceStatusLabel: Record<DeviceStatus, string> = {
  online: "在线",
  offline: "离线",
  unauthorized: "未授权",
  permission_denied: "权限不足",
  error: "异常",
};

export const deviceStatusColor: Record<DeviceStatus, BadgeProps["status"]> = {
  online: "success",
  offline: "default",
  unauthorized: "warning",
  permission_denied: "warning",
  error: "error",
};
