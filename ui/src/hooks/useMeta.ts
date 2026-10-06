import { useQuery } from "@tanstack/react-query";
import { getMeta, systemKeys } from "@/api/system";

/** /api/meta (workspace, versions, fake_cloud). Cached for the session; refetched on focus. */
export function useMeta() {
  return useQuery({ queryKey: systemKeys.meta, queryFn: getMeta, staleTime: 60_000, retry: 1 });
}
