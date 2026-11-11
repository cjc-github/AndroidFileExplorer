import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * ``request.ts`` runs its ``?token=`` bootstrap at import time, so every
 * scenario needs a fresh module evaluation after the URL is staged.
 */
async function importFresh() {
  vi.resetModules();
  return import("./request");
}

describe("extractErrorMessage", () => {
  it("reads the backend detail field", async () => {
    const { extractErrorMessage } = await importFresh();
    const axiosLike = {
      isAxiosError: true,
      response: { data: { detail: "该路径需要 root 权限" } },
    };
    expect(extractErrorMessage(axiosLike)).toBe("该路径需要 root 权限");
  });

  it("falls back to the message field", async () => {
    const { extractErrorMessage } = await importFresh();
    const axiosLike = {
      isAxiosError: true,
      response: { data: { message: "设备不在线" } },
    };
    expect(extractErrorMessage(axiosLike)).toBe("设备不在线");
  });

  it("handles plain errors", async () => {
    const { extractErrorMessage } = await importFresh();
    expect(extractErrorMessage(new Error("boom"))).toBe("boom");
    // A bare string is not an Error instance: the generic fallback applies.
    expect(extractErrorMessage("raw string")).toBe("请求失败");
  });
});

describe("isPermissionError", () => {
  it("matches the permission family in both languages", async () => {
    const { isPermissionError } = await importFresh();
    expect(isPermissionError(new Error("Permission denied"))).toBe(true);
    expect(isPermissionError(new Error("权限不足：无法访问 /system"))).toBe(true);
    expect(isPermissionError(new Error("设备不存在"))).toBe(false);
  });
});

describe("the ?token= bootstrap", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    window.history.replaceState({}, "", "/");
    vi.restoreAllMocks();
  });

  it("stores the token and strips it from the address bar", async () => {
    window.history.replaceState({}, "", "/?token=abc123");
    const mod = await importFresh();
    expect(localStorage.getItem("afe_api_token")).toBe("abc123");
    expect(window.location.search).toBe("");
    expect(mod.getApiToken()).toBe("abc123");
  });

  it("keeps working without a token in the URL", async () => {
    window.history.replaceState({}, "", "/");
    const mod = await importFresh();
    expect(localStorage.getItem("afe_api_token")).toBeNull();
    expect(mod.getApiToken()).toBe("");
  });

  it("registers one request interceptor for the auth header", async () => {
    window.history.replaceState({}, "", "/");
    const mod = await importFresh();
    expect(mod.request.interceptors.request.handlers).toHaveLength(1);
  });
});
