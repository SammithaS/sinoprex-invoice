"use client";

import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, ChevronLeft, Download, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Pill, StatusBadge } from "@/components/ui/badges";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { apiPost, backendUrl, type Run } from "@/lib/api/client";
import { asDecision } from "@/lib/run-state";
import { useRun } from "@/lib/use-run";
import { formatINR } from "@/lib/utils";
import { ClausesCard, FieldsCard, RulesCard, TouchlessCard } from "./checks";
import { DecisionCard } from "./decision-card";
import { PdfPreview } from "./pdf-preview";
import { deriveStages, Timeline } from "./timeline";

const RUN_STATUS: Record<string, { label: string; tone: "brand" | "review" | "rejected" | "neutral" }> = {
  QUEUED: { label: "Queued", tone: "neutral" },
  RUNNING: { label: "Running", tone: "brand" },
  WAITING_REVIEW: { label: "Waiting for review", tone: "review" },
  FAILED: { label: "Failed", tone: "rejected" },
};

function cycle(ms: number | null | undefined, reviewed: boolean) {
  if (!ms) return null;
  const t = ms < 60_000 ? `${(ms / 1000).toFixed(1)} s` : `${(ms / 60_000).toFixed(1)} min`;
  return `cycle time ${t}${reviewed ? " incl. review" : ""}`;
}

export function RunView() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { run, state, review, events, status, isPending, error } = useRun(id);
  const rerun = useMutation({
    mutationFn: () => apiPost<Run>(`/runs/${id}/rerun`),
    onSuccess: (r) => router.push(`/runs/${r.id}`),
  });

  if (isPending) {
    return (
      <div className="grid gap-5 lg:grid-cols-3" aria-busy="true">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-96 animate-pulse rounded-card bg-card shadow-card" />
        ))}
      </div>
    );
  }
  if (error || !run) {
    return (
      <Card role="alert" className="flex flex-col items-start gap-2">
        <span className="flex items-center gap-2 font-semibold text-rejected">
          <AlertTriangle aria-hidden className="size-4" /> Couldn&apos;t load this run
        </span>
        <p className="text-muted">{error?.message ?? "Not found"}</p>
        <Button asChild variant="soft" size="sm">
          <Link href="/">Start a new run</Link>
        </Button>
      </Card>
    );
  }

  const decision = asDecision(run.final_decision ?? run.decision);
  const stages = deriveStages(events, status);
  const current = stages.find((s) => s.state === "running")?.label ?? null;
  const terminal = status === "COMPLETED" || status === "FAILED";
  const showBadge = status === "COMPLETED" && decision;
  const runBadge = RUN_STATUS[status ?? ""];
  const title = run.invoice_number ?? run.file_name;
  const meta = [
    run.vendor_name,
    run.total ? formatINR(run.total) : null,
    run.procedure_version ? `procedure ${run.procedure_version}` : null,
    terminal ? cycle(run.duration_ms, run.reviews.length > 0) : null,
  ].filter(Boolean);

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-1.5">
          <Link
            href="/dashboard"
            className="inline-flex min-h-6 items-center gap-1 text-[13px] font-medium text-brand-700 hover:text-brand-800 dark:text-brand-500"
          >
            <ChevronLeft aria-hidden className="size-3.5" /> Dashboard
          </Link>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="font-mono text-[26px] leading-9 font-semibold tracking-[-0.01em] break-all text-navy">
              {title}
            </h1>
            {showBadge ? (
              <StatusBadge decision={decision} />
            ) : runBadge ? (
              <Pill tone={runBadge.tone}>{runBadge.label}</Pill>
            ) : null}
            {status === "WAITING_REVIEW" && decision && <StatusBadge decision={decision} />}
          </div>
          <span className="text-muted">{meta.join(" · ") || run.file_name}</span>
        </div>
        <div className="flex flex-wrap gap-2.5">
          <Button asChild variant="ghost">
            <a href={backendUrl(`/runs/${id}/file`)} target="_blank" rel="noreferrer">
              <Download aria-hidden /> Original PDF
            </a>
          </Button>
          <Button variant="soft" disabled={!terminal || rerun.isPending} onClick={() => rerun.mutate()}>
            <RotateCcw aria-hidden /> Re-run
          </Button>
        </div>
      </div>

      <div className="grid items-start gap-5 lg:grid-cols-2 2xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.3fr)]">
        <PdfPreview
          runId={id}
          fileName={run.file_name}
          pages={state.document?.page_count ?? 1}
          extraction={state.extraction}
        />
        <Timeline events={events} runStatus={status} />
        <div className="flex flex-col gap-5 lg:col-span-2 2xl:col-span-1">
          <DecisionCard run={run} state={state} review={review} status={status} currentStage={current} />
          {state.assessment && <TouchlessCard assessment={state.assessment} />}
          {state.assessment && (
            <ClausesCard clauses={state.clauses ?? []} cited={state.assessment.citations} />
          )}
          {state.extraction && <FieldsCard extraction={state.extraction} />}
          {state.assessment && <RulesCard assessment={state.assessment} />}
        </div>
      </div>
    </div>
  );
}
