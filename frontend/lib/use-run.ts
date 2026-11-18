"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { apiGet, backendUrl, type RunDetail } from "@/lib/api/client";
import type { CaseState, ReviewRequest } from "@/lib/run-state";

/** One timeline event from GET /runs/{id}/events (persisted steps replay first, then live). */
export type RunEvent = {
  seq?: number;
  type: "step_started" | "step_finished" | "rule" | "paused" | "status" | "ping" | string;
  node?: string | null;
  status?: string | null;
  message?: string | null;
  label?: string | null;
  duration_ms?: number | null;
  code?: string;
  name?: string;
  citations?: string[];
  data?: Record<string, unknown> | null;
  decision?: string | null;
  final_decision?: string | null;
  error?: string | null;
};

const TERMINAL = new Set(["COMPLETED", "FAILED"]);
// After these steps the run snapshot has new state worth refetching.
const REFRESH_AFTER = new Set(["extract", "vendor_match", "retrieve_policy", "rules", "human_review", "explain", "close_case"]);

export function useRun(id: string) {
  const queryClient = useQueryClient();
  const detail = useQuery({
    queryKey: ["run", id],
    queryFn: () => apiGet<RunDetail>(`/runs/${id}`),
    staleTime: 0,
  });
  // Keyed by run id so navigating to another run starts from an empty timeline.
  const [stream, setStream] = useState<{ id: string; events: RunEvent[] }>({ id, events: [] });
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const source = new EventSource(backendUrl(`/runs/${id}/events`));
    const seen = new Set<number>();
    const refresh = () => void queryClient.invalidateQueries({ queryKey: ["run", id] });

    const onEvent = (raw: MessageEvent<string>) => {
      let event: RunEvent;
      try {
        event = JSON.parse(raw.data) as RunEvent;
      } catch {
        return;
      }
      if (event.type === "ping") return;
      if (event.seq !== undefined) {
        if (seen.has(event.seq)) return;
        seen.add(event.seq);
      }
      setStream((prev) => (prev.id === id ? { id, events: [...prev.events, event] } : { id, events: [event] }));
      if (event.type === "step_finished" && event.node && REFRESH_AFTER.has(event.node)) refresh();
      if (event.type === "paused" || event.type === "status") refresh();
      if (event.type === "status" && event.status && TERMINAL.has(event.status)) {
        source.close(); // otherwise EventSource reconnects after the server ends the stream
        setConnected(false);
      }
    };
    for (const type of ["step_started", "step_finished", "rule", "paused", "status", "ping", "message"]) {
      source.addEventListener(type, onEvent as EventListener);
    }
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    return () => source.close();
  }, [id, queryClient]);
  const events = useMemo(() => (stream.id === id ? stream.events : []), [stream, id]);

  const run = detail.data;
  const state = (run?.state ?? {}) as CaseState;
  const review = (run?.review_request ?? null) as ReviewRequest | null;
  const live = useMemo(() => {
    const last = [...events].reverse().find((e) => e.type === "status");
    return last?.status ?? run?.status ?? null;
  }, [events, run?.status]);

  return {
    run,
    state,
    review,
    events,
    status: live,
    connected,
    isPending: detail.isPending,
    error: detail.error,
  };
}
