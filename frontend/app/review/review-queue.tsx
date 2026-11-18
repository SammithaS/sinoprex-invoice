"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ClipboardCheck, ExternalLink } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Pill, StatusBadge } from "@/components/ui/badges";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ReviewForm } from "@/components/review-form";
import { apiGet, type Run, type RunDetail } from "@/lib/api/client";
import { asDecision, type ReviewRequest } from "@/lib/run-state";
import { cn, formatINR } from "@/lib/utils";

export function ago(iso: string): string {
  const mins = Math.max(0, Math.round((Date.now() - new Date(`${iso}Z`).getTime()) / 60000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  return hrs < 24 ? `${hrs} hr ago` : `${Math.round(hrs / 24)} d ago`;
}

export function ReviewQueue() {
  const runs = useQuery({
    queryKey: ["runs", "WAITING_REVIEW"],
    queryFn: () => apiGet<Run[]>("/runs?status=WAITING_REVIEW"),
    refetchInterval: 15_000,
  });

  if (runs.isPending) {
    return (
      <div className="flex flex-col gap-3" aria-busy="true">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-28 animate-pulse rounded-card bg-card shadow-card" />
        ))}
      </div>
    );
  }
  if (runs.error) {
    return (
      <Card role="alert" className="text-rejected">
        Couldn&apos;t load the queue: {runs.error.message}
      </Card>
    );
  }
  if (runs.data.length === 0) {
    return (
      <Card className="flex flex-col items-center gap-3 py-12 text-center">
        <span className="flex size-12 items-center justify-center rounded-control bg-approved-bg text-approved">
          <ClipboardCheck aria-hidden className="size-6" />
        </span>
        <h2 className="text-lg font-semibold text-navy">Nothing waiting for review</h2>
        <p className="max-w-sm text-muted">Cases the rules can&apos;t decide on their own appear here, oldest first.</p>
        <Button asChild variant="soft" size="sm">
          <Link href="/">Run a sample</Link>
        </Button>
      </Card>
    );
  }
  const oldestFirst = [...runs.data].sort((a, b) => a.created_at.localeCompare(b.created_at));
  return (
    <ul className="flex flex-col gap-3">
      {oldestFirst.map((r) => (
        <QueueItem key={r.id} run={r} />
      ))}
    </ul>
  );
}

function QueueItem({ run }: { run: Run }) {
  const [open, setOpen] = useState(false);
  const detail = useQuery({
    queryKey: ["run", run.id],
    queryFn: () => apiGet<RunDetail>(`/runs/${run.id}`),
  });
  const request = (detail.data?.review_request ?? null) as ReviewRequest | null;
  const decision = asDecision(run.decision);

  return (
    <li>
      <Card className="flex flex-col gap-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 flex-col gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <Link href={`/runs/${run.id}`} className="font-mono font-semibold text-navy hover:underline">
                {run.invoice_number ?? run.file_name}
              </Link>
              {decision && <StatusBadge decision={decision} />}
              {request?.exceptions.map((e) => (
                <Pill key={e.rule_code} tone="neutral" className="font-mono">
                  {e.category}
                </Pill>
              ))}
            </div>
            <span className="text-[13px] text-muted">
              {[run.vendor_name, run.total ? formatINR(run.total) : null, `waiting since ${ago(run.created_at)}`]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </div>
          <div className="flex gap-2">
            <Button asChild variant="ghost" size="sm">
              <Link href={`/runs/${run.id}`} aria-label={`Open ${run.invoice_number ?? run.file_name}`}>
                <ExternalLink aria-hidden /> Open
              </Link>
            </Button>
            <Button
              variant="soft"
              size="sm"
              onClick={() => setOpen((o) => !o)}
              aria-expanded={open}
              aria-label={`Review ${run.invoice_number ?? run.file_name}`}
            >
              Review <ChevronDown aria-hidden className={cn("transition-transform", open && "rotate-180")} />
            </Button>
          </div>
        </div>
        {request && (
          <div className="rounded-control bg-[#FFFBEB] px-3.5 py-2.5 dark:bg-review-bg">
            <span className="text-xs font-semibold text-brand-800">Question · </span>
            <span className="text-navy">{request.headline}</span>
          </div>
        )}
        {open && <ReviewForm runId={run.id} />}
      </Card>
    </li>
  );
}
