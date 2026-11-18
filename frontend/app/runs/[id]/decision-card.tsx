"use client";

import { AlertTriangle, ExternalLink, Loader2, Pause, ShieldCheck, Sparkles, UserCheck } from "lucide-react";
import Link from "next/link";
import { CitationChip, StatusBadge, type Decision } from "@/components/ui/badges";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ReviewForm } from "@/components/review-form";
import type { RunDetail } from "@/lib/api/client";
import { asDecision, type CaseState, type ReviewRequest } from "@/lib/run-state";
import { cn } from "@/lib/utils";

const BORDER: Record<Decision, string> = {
  APPROVED: "border-t-approved",
  NEEDS_REVIEW: "border-t-review",
  ON_HOLD: "border-t-hold",
  REJECTED: "border-t-rejected",
};

/** Turn "[ap-policy §3.2]" in prose into citation chips. */
function WithCitations({ text }: { text: string }) {
  const parts = text.split(/(\[[^\]]+\])/g);
  return (
    <>
      {parts.map((p, i) => (/^\[[^\]]+\]$/.test(p) ? <CitationChip key={i}>{p}</CitationChip> : <span key={i}>{p}</span>))}
    </>
  );
}

function Question({ children, extra }: { children: string; extra?: string[] }) {
  return (
    <div className="flex flex-col gap-1 rounded-control bg-[#FFFBEB] px-3.5 py-3 dark:bg-review-bg">
      <span className="text-xs font-semibold text-brand-800">Question for you</span>
      <span className="text-navy">{children}</span>
      {extra?.map((q) => (
        <span key={q} className="text-[13px] text-ink">
          Also: {q}
        </span>
      ))}
    </div>
  );
}

export function DecisionCard({
  run,
  state,
  review,
  status,
  currentStage,
}: {
  run: RunDetail;
  state: CaseState;
  review: ReviewRequest | null;
  status: string | null;
  currentStage: string | null;
}) {
  const decision = asDecision(run.final_decision ?? run.decision);

  if (status === "FAILED") {
    return (
      <Card className="flex flex-col gap-3 border-t-4 border-t-rejected">
        <div className="flex items-center gap-3">
          <span className="flex size-10 items-center justify-center rounded-control bg-rejected-bg text-rejected">
            <AlertTriangle aria-hidden className="size-5" />
          </span>
          <h2 className="text-lg font-semibold text-navy">The run failed</h2>
        </div>
        <p className="font-mono text-[13px] break-words text-rejected">{run.error ?? state.errors?.join("; ")}</p>
        <p className="text-muted">Nothing was decided. Fix the cause (often an LLM quota or a bad file) and re-run.</p>
      </Card>
    );
  }

  if (status === "WAITING_APPROVAL" && review?.approval) {
    const a = review.approval;
    return (
      <Card className={cn("flex flex-col gap-3.5 border-t-4", BORDER[review.decision])}>
        <div className="flex items-center gap-3">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-control bg-hold-bg text-hold">
            <UserCheck aria-hidden className="size-5" />
          </span>
          <div className="flex min-w-0 flex-col">
            <h2 className="text-lg font-semibold text-navy">Waiting for approval</h2>
            <span className="text-[13px] text-muted">
              L{a.level} · {a.role}
              {a.approver_name ? ` (${a.approver_name})` : ""}
            </span>
          </div>
        </div>
        <p className="leading-6">{a.reason}</p>
        {a.citation && <CitationChip>{`[${a.citation}]`}</CitationChip>}
        {(review.already_involved ?? []).length > 0 && (
          <p className="flex items-center gap-1.5 text-xs text-muted">
            <ShieldCheck aria-hidden className="size-3.5" /> Four-eyes: {review.already_involved!.join(", ")} can&apos;t
            take this approval.
          </p>
        )}
        <Button asChild className="self-start">
          <Link href="/approvals">
            <ExternalLink aria-hidden /> Open the approvals inbox
          </Link>
        </Button>
      </Card>
    );
  }

  if (status === "WAITING_REVIEW" && review?.kind === "erp") {
    return (
      <Card className="flex flex-col gap-3.5 border-t-4 border-t-rejected">
        <div className="flex items-center gap-3">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-control bg-rejected-bg text-rejected">
            <AlertTriangle aria-hidden className="size-5" />
          </span>
          <div className="flex min-w-0 flex-col">
            <h2 className="text-lg font-semibold text-navy">ERP posting doesn&apos;t match</h2>
            <span className="text-[13px] text-muted">ERP_MISMATCH → AP Manager · case stays open</span>
          </div>
        </div>
        <ul className="flex flex-col gap-1 text-[13px] text-rejected">
          {(review.mismatches ?? []).map((m) => (
            <li key={m}>{m}</li>
          ))}
        </ul>
        <Question>{review.headline}</Question>
        <CitationChip>[ap-policy §10.1]</CitationChip>
        <div className="flex flex-wrap items-center gap-3">
          <Button asChild variant="soft">
            <Link href="/erp">
              <ExternalLink aria-hidden /> Fix it in the ERP ledger
            </Link>
          </Button>
        </div>
        <ReviewForm runId={run.id} actions={["RECHECK"]} />
      </Card>
    );
  }

  if (status === "WAITING_REVIEW" && review) {
    const exc = review.exceptions;
    const needsSecond = review.override_needs_second_approver ?? [];
    return (
      <Card className={cn("flex flex-col gap-3.5 border-t-4", BORDER[review.decision])}>
        <div className="flex items-center gap-3">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-control bg-brand-50 text-brand-700 dark:text-brand-500">
            <Pause aria-hidden className="size-5" />
          </span>
          <div className="flex min-w-0 flex-col">
            <h2 className="text-lg font-semibold text-navy">Waiting for your review</h2>
            <span className="text-[13px] text-muted">
              {exc.length > 0
                ? exc.map((e) => `${e.category} → ${e.owner_role}${e.owner_name ? ` (${e.owner_name})` : ""}`).join(" · ")
                : "No exception category"}
            </span>
          </div>
        </div>
        <ul className="flex flex-col gap-1.5">
          {review.reasons.map((r) => (
            <li key={r} className="leading-6">
              {r}
            </li>
          ))}
        </ul>
        <Question extra={exc.slice(1).map((e) => e.question)}>{review.headline}</Question>
        {needsSecond.length > 0 && (
          <p className="flex items-center gap-1.5 text-xs text-muted">
            <ShieldCheck aria-hidden className="size-3.5" /> Approving despite {needsSecond.join(", ")} is an
            override: a second approver signs it off next (four-eyes).
          </p>
        )}
        {review.citations.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {review.citations.map((c) => (
              <CitationChip key={c}>{`[${c}]`}</CitationChip>
            ))}
          </div>
        )}
        <ReviewForm runId={run.id} actions={review.actions} />
      </Card>
    );
  }

  if (status === "COMPLETED" && decision) {
    const last = run.reviews.at(-1);
    return (
      <Card className={cn("flex flex-col gap-3.5 border-t-4", BORDER[decision])}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold text-navy">Decision</h2>
          <StatusBadge decision={decision} />
        </div>
        {last && last.action !== "RECHECK" && (
          <p className="text-[13px] text-muted">
            Rules said {run.decision?.replace("_", " ").toLowerCase()}; {last.reviewer ?? "a reviewer"} chose{" "}
            {last.action.toLowerCase().replace("_", " ")}
            {last.comment ? ` — “${last.comment}”` : ""}.
          </p>
        )}
        {state.explanation && (
          <p className="leading-6">
            <WithCitations text={state.explanation} />
          </p>
        )}
        {state.next_action && (
          <div className="flex gap-2 rounded-control bg-canvas px-3.5 py-3 text-[13px]">
            <Sparkles aria-hidden className="mt-0.5 size-4 shrink-0 text-brand-500" />
            <span>
              <span className="font-semibold text-navy">Next: </span>
              {state.next_action}
            </span>
          </div>
        )}
      </Card>
    );
  }

  return (
    <Card className="flex items-center gap-3 border-t-4 border-t-brand-300">
      <Loader2 aria-hidden className="size-5 animate-spin text-brand-500" />
      <div className="flex flex-col">
        <h2 className="text-lg font-semibold text-navy">Working on it…</h2>
        <span className="text-[13px] text-muted">{currentStage ?? "Queued"}</span>
      </div>
    </Card>
  );
}
