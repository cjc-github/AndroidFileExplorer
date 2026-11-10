import axios from "axios";

import type { ApiEnvelope } from "./types";

export const request = axios.create({
  baseURL: "/api/v1",
  timeout: 60_000,
});

/**
 * Extract a human readable message from a failed request.
 *
 * The backend answers errors with FastAPI's default shape
 * ``{"detail": "..."}`` inside the HTTP body, so the text lives at
 * ``error.response.data.detail`` - NOT at ``error.response.detail``.
 * Reading the wrong level silently falls back to axios' generic
 * "Request failed with status code 403" message and hides the real reason
 * (for example "该路径需要 root 权限").
 */
export function extractErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data as
      | { detail?: unknown; message?: unknown }
      | string
      | undefined;

    if (typeof data === "string" && data.trim()) {
      // The body may still be a JSON string with a "detail" field.
      try {
        const parsed = JSON.parse(data) as { detail?: unknown };
        if (typeof parsed.detail === "string") return parsed.detail;
      } catch {
        // Not JSON: use the raw text.
      }
      return data;
    }

    if (data && typeof data === "object") {
      if (typeof data.detail === "string" && data.detail) return data.detail;
      if (typeof data.message === "string" && data.message) return data.message;
    }

    return error.message || "请求失败";
  }

  if (error instanceof Error) {
    return error.message || "请求失败";
  }

  return "请求失败";
}

/** True when the failure is caused by a missing permission / root access. */
export function isPermissionError(error: unknown): boolean {
  const message = extractErrorMessage(error);
  return /权限|permission denied|root|unauthorized/i.test(message);
}

/** Unwraps the backend envelope and rejects with the backend message. */
export async function unwrap<T>(promise: Promise<{ data: ApiEnvelope<T> }>): Promise<T> {
  const response = await promise;
  const envelope = response.data;
  if (envelope.code !== 200) {
    throw new Error(envelope.message || "请求失败");
  }
  return envelope.data as T;
}
