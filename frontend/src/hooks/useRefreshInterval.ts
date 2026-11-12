import { useQuery } from "@tanstack/react-query";

import { getSettings } from "@/api";

/**
 * Poll interval for every page that shows live device state.
 *
 * The platform setting is the single source of truth; the fallback keeps the
 * pages usable when the settings request has not completed yet.
 */
export function useRefreshInterval(defaultSeconds = 3): number {
  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: getSettings,
    staleTime: 30_000,
  });
  const seconds = settingsQuery.data?.sync_interval_seconds ?? defaultSeconds;
  return Math.max(1, seconds) * 1000;
}
