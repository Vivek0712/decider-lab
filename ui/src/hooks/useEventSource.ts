import { useEffect, useRef, useState } from "react";
import { eventData } from "@/lib/api";

export type SseState = "connecting" | "open" | "closed" | "error";

/**
 * Subscribe to named SSE events. `open` creates the EventSource (e.g. () => jobEvents(id)); the
 * handlers object may change between renders without reconnecting. Closes on unmount, on an `end`
 * event, and when `enabled` becomes false.
 *
 *   useEventSource(() => jobEvents(jobId, { from: 0 }), {
 *     log: (line: LogLine) => append(line),
 *     status: (s) => ...,
 *   }, { enabled: !!jobId, deps: [jobId] });
 */
export function useEventSource(
  open: () => EventSource,
  handlers: Record<string, (data: any) => void>,
  opts: { enabled?: boolean; deps?: unknown[] } = {},
): SseState {
  const [state, setState] = useState<SseState>("connecting");
  const ref = useRef(handlers);
  ref.current = handlers;
  const enabled = opts.enabled ?? true;

  useEffect(() => {
    if (!enabled) {
      setState("closed");
      return;
    }
    const es = open();
    setState("connecting");
    es.onopen = () => setState("open");
    es.onerror = () => setState(es.readyState === EventSource.CLOSED ? "closed" : "error");
    const names = new Set([...Object.keys(ref.current), "end"]);
    const listeners: Array<[string, (e: MessageEvent) => void]> = [];
    for (const name of names) {
      const fn = (e: MessageEvent) => {
        if (name === "end") {
          es.close();
          setState("closed");
        }
        ref.current[name]?.(eventData(e));
      };
      es.addEventListener(name, fn as EventListener);
      listeners.push([name, fn]);
    }
    return () => {
      for (const [n, fn] of listeners) es.removeEventListener(n, fn as EventListener);
      es.close();
    };
  }, [enabled, ...(opts.deps ?? [])]);

  return state;
}
