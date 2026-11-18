"use client";

import { Check, ChevronDown, CircleAlert, Minus, Pause, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Pill } from "@/components/ui/badges";
import type { RunEvent } from "@/lib/use-run";
import { cn } from "@/lib/utils";
import { RuleRow } from "./checks";

export const STAGES: { node: string; label: string }[] = [
  { node: "intake", label: "Receive & register case" },
  { node: "document_check", label: "Check document" },
  { node: "extract", label: "Extract fields with evidence" },
  { node: "vendor_match", label: "Identify supplier" },
  { node: "retrieve_policy", label: "Retrieve contract & policy" },
  { node: "rules", label: "Validate, match & decide" },
  { node: "register_case", label: "Register in invoice history" },
  { node: "route_exception", label: "Route exceptions to owners" },
  { node: "human_review", label: "Human review" },
  { node: "approval_routing", label: "Approval routing" },
  { node: "post_erp", label: "Post to ERP" },
  { node: "verify_post", label: "Verify posting (read-back)" },
  { node: "explain", label: "Explain decision" },
  { node: "close_case", label: "Close case" },
];

type StageState = "pending" | "running" | "done" | "warn" | "failed" | "paused" | "skipped";

type Stage = {
  node: string;
  label: string;
  state: StageState;
  message: string | null;
  ms: number | null;
};

export function deriveStages(events: RunEvent[], runStatus: string | null): Stage[] {
  const terminal = runStatus === "COMPLETED" || runStatus === "FAILED";
  return STAGES.map(({ node, label }) => {
    const mine = events.filter((e) => e.node === node);
    const finished = [...mine].reverse().find((e) => e.type === "step_finished");
    // A node that pauses runs again when resumed: it is paused only if that's its latest event.
    const last = mine.at(-1);
    const waiting = runStatus === "WAITING_REVIEW" || runStatus === "WAITING_APPROVAL";
    const paused = last?.type === "paused" && waiting;
    const lastStart = [...mine].reverse().find((e) => e.type === "step_started");
    let state: StageState = "pending";
    if (paused) {
      state = "paused";
    } else if (finished && (!lastStart || (finished.seq ?? 0) > (lastStart.seq ?? 0))) {
      const s = finished.status;
      state = s === "warn" ? "warn" : s === "failed" ? "failed" : s === "skipped" ? "skipped" : "done";
    } else if (lastStart && !terminal) {
      state = "running";
    } else if (terminal) {
      state = "skipped";
    }
    const pausedMsg = [...mine].reverse().find((e) => e.type === "paused")?.message ?? null;
    return {
      node,
      label,
      state,
      message: state === "paused" ? `Waiting for you: ${pausedMsg ?? ""}` : (finished?.message ?? null),
      ms: finished?.duration_ms ?? null,
    };
  });
}

const DOT: Record<StageState, string> = {
  done: "bg-approved-bg text-approved",
  warn: "bg-review-bg text-review",
  failed: "bg-rejected-bg text-rejected",
  paused: "bg-gradient-to-br from-brand-500 to-brand-600 text-white ring-4 ring-brand-100",
  running: "bg-gradient-to-br from-brand-500 to-brand-600 text-white ring-4 ring-brand-100 animate-pulse",
  pending: "border border-dashed border-muted/50 bg-canvas text-muted",
  skipped: "bg-canvas text-muted",
};

function DotIcon({ state }: { state: StageState }) {
  const cls = "size-3";
  switch (state) {
    case "done":
      return <Check aria-hidden className={cls} strokeWidth={3} />;
    case "warn":
      return <CircleAlert aria-hidden className={cls} strokeWidth={3} />;
    case "failed":
      return <X aria-hidden className={cls} strokeWidth={3} />;
    case "paused":
      return <Pause aria-hidden className={cls} strokeWidth={3} />;
    case "skipped":
      return <Minus aria-hidden className={cls} strokeWidth={3} />;
    default:
      return <span className="size-1.5 rounded-full bg-current" />;
  }
}

const STATE_TEXT: Record<StageState, string> = {
  done: "done",
  warn: "warning",
  failed: "failed",
  paused: "waiting for review",
  running: "running",
  pending: "pending",
  skipped: "skipped",
};

function seconds(ms: number | null): string {
  if (ms === null) return "";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function Timeline({ events, runStatus }: { events: RunEvent[]; runStatus: string | null }) {
  const stages = deriveStages(events, runStatus);
  const [open, setOpen] = useState<string | null>("rules");
  const rules = events.filter((e) => e.type === "rule");
  const done = stages.filter((s) => ["done", "warn", "failed", "skipped"].includes(s.state)).length;

  return (
    <Card className="flex flex-col">
      <CardHeader>
        <CardTitle>Pipeline</CardTitle>
        <Pill tone="brand">
          {done} of {stages.length} done
        </Pill>
      </CardHeader>
      <ol className="flex flex-col" aria-label="Pipeline stages">
        {stages.map((s, i) => {
          const expandable = s.node === "rules" && rules.length > 0;
          const isOpen = open === s.node && expandable;
          return (
            <li key={s.node} className="flex gap-3">
              <div className="flex w-6 flex-col items-center">
                <motion.span
                  key={s.state}
                  initial={{ scale: 0.6, opacity: 0.4 }}
                  animate={{ scale: 1, opacity: 1 }}
                  transition={{ type: "spring", stiffness: 420, damping: 22 }}
                  className={cn("flex size-6 shrink-0 items-center justify-center rounded-full", DOT[s.state])}
                >
                  <DotIcon state={s.state} />
                </motion.span>
                {i < stages.length - 1 && (
                  <span
                    aria-hidden
                    className={cn(
                      "min-h-3 w-0.5 flex-1",
                      ["done", "warn", "skipped"].includes(s.state) ? "bg-brand-300" : "bg-line",
                    )}
                  />
                )}
              </div>
              <div className="flex min-w-0 flex-1 flex-col gap-1 pb-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex min-w-0 flex-col">
                    {expandable ? (
                      <button
                        type="button"
                        onClick={() => setOpen(isOpen ? null : s.node)}
                        aria-expanded={isOpen}
                        className="flex min-h-6 items-center gap-1 text-left font-medium text-navy"
                      >
                        {s.label}
                        <ChevronDown aria-hidden className={cn("size-4 transition-transform", isOpen && "rotate-180")} />
                      </button>
                    ) : (
                      <span className={cn("font-medium", s.state === "pending" ? "text-muted" : "text-navy")}>
                        {s.label}
                      </span>
                    )}
                    <span className="sr-only">: {STATE_TEXT[s.state]}</span>
                    {s.message && (
                      <span
                        className={cn(
                          "text-xs leading-[18px] break-words",
                          s.state === "warn" || s.state === "paused" ? "text-review" : "text-muted",
                          s.state === "failed" && "text-rejected",
                        )}
                      >
                        {s.message}
                      </span>
                    )}
                    {s.state === "running" && <span className="text-xs text-brand-800">Working…</span>}
                  </div>
                  <span className="text-xs whitespace-nowrap text-muted">{seconds(s.ms)}</span>
                </div>
                <AnimatePresence initial={false}>
                  {isOpen && (
                    <motion.ul
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      className="flex flex-col gap-2 overflow-hidden rounded-control bg-canvas p-3"
                    >
                      {rules.map((r) => (
                        <RuleRow
                          key={`${r.seq}-${r.code}`}
                          status={(r.status ?? "SKIP") as "PASS" | "WARN" | "FAIL" | "SKIP"}
                          name={r.name ?? r.code ?? ""}
                          message={r.message ?? ""}
                          citations={r.citations ?? []}
                          compact
                        />
                      ))}
                    </motion.ul>
                  )}
                </AnimatePresence>
              </div>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
