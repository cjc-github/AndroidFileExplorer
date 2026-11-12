import { useQuery } from "@tanstack/react-query";

import { getEnvironmentCheck } from "@/api";

/** Shared environment diagnostics query (settings / device page hints). */
export function useEnvironmentCheck() {
  return useQuery({
    queryKey: ["environment-check"],
    queryFn: getEnvironmentCheck,
    staleTime: 30_000,
  });
}
