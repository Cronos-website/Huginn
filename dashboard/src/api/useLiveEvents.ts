import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, getToken } from "./client";

// Map a server hint to the query-key prefixes to invalidate (prefix match, so
// ["vm"] refreshes every ["vm", id]).
const INVALIDATE: Record<string, string[][]> = {
  tasks: [["audit"], ["vms"], ["vm"]],
  vms: [["vms"], ["vm"]],
};

/**
 * Subscribe to the hub's SSE stream and refresh the matching React Query caches
 * the instant something changes server-side (a worker reports a result, a VM
 * goes offline…), instead of waiting for the next poll. EventSource reconnects
 * automatically; the existing polling stays as a fallback.
 */
export function useLiveEvents(): void {
  const qc = useQueryClient();
  useEffect(() => {
    const token = getToken();
    if (!token) return;
    const source = new EventSource(`${api.hubUrl}/api/events?token=${encodeURIComponent(token)}`);
    source.onmessage = (e) => {
      try {
        const { type } = JSON.parse(e.data) as { type?: string };
        for (const key of INVALIDATE[type ?? ""] ?? []) {
          qc.invalidateQueries({ queryKey: key });
        }
      } catch {
        /* ignore malformed events */
      }
    };
    return () => source.close();
  }, [qc]);
}
