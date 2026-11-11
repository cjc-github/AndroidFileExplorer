import axios from "axios";

import type { ApiEnvelope } from "./types";

const TOKEN_STORAGE_KEY = "afe_api_token";

/**
 * Pick up an API token handed in via ``?token=`` (bookmarkable login), keep
 * it in localStorage and remove it from the address bar.
 *
 * The backend only checks tokens when ``API_AUTH_TOKEN`` is configured; with
 * no token stored nothing is attached and requests behave as before.
 */
(function bootstrapApiToken(): void {
  const url = new URL(window.location.href);
  const queryToken = url.searchParams.get("token");
  if (queryToken) {
    localStorage.setItem(TOKEN_STORAGE_KEY, queryToken);
    url.searchParams.delete("token");
    window.history.replaceState({}, "", url.toString());
  }
})();

export function getApiToken(): string {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

export const request = axios.create({
  baseURL: "/api/v1",
  timeout: 60_000,
});

request.interceptors.request.use((config) => {
  const token = getApiToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
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
