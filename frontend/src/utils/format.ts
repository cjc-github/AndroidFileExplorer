/** Formatting helpers shared by the pages. */

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "-";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return `${value.toFixed(1)}%`;
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString("zh-CN", { hour12: false });
}
