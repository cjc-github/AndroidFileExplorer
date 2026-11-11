import { describe, expect, it } from "vitest";

import { formatBytes, formatPercent, formatTime } from "./format";

describe("formatBytes", () => {
  it("passes null through as a dash", () => {
    expect(formatBytes(null)).toBe("-");
    expect(formatBytes(undefined)).toBe("-");
  });

  it("keeps small values in bytes", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
  });

  it("scales up the unit ladder", () => {
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(1024 * 1024 * 1024)).toBe("1.0 GB");
  });

  it("never leaves the last unit", () => {
    // 4 PB is beyond the ladder; it must clamp, not crash.
    expect(formatBytes(4 * 1024 ** 5)).toMatch(/TB$/);
  });
});

describe("formatPercent", () => {
  it("passes null through as a dash", () => {
    expect(formatPercent(null)).toBe("-");
  });

  it("formats one decimal", () => {
    expect(formatPercent(12.34)).toBe("12.3%");
  });
});

describe("formatTime", () => {
  it("passes empty values through as a dash", () => {
    expect(formatTime(null)).toBe("-");
    expect(formatTime("")).toBe("-");
  });

  it("rejects garbage instead of printing 'Invalid Date'", () => {
    expect(formatTime("not-a-date")).toBe("-");
  });

  it("formats a real ISO timestamp", () => {
    const text = formatTime("2024-01-02T13:45:00");
    expect(text).toContain("2024");
    expect(text).not.toBe("-");
  });
});
