"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ExternalLink, Loader2, ShieldCheck, UserCheck, X } from "lucide-react";
import Link from "next/link";
import { useId, useState } from "react";
import { CitationChip, Pill, StatusBadge } from "@/components/ui/badges";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { apiGet, apiPost, type InboxItem, type Run } from "@/lib/api/client";
import { asDecision } from "@/lib/run-state";
import { cn, formatINR } from "@/lib/utils";
import { ago } from "../review/review-queue";

type Chain = { id: number; level: number; role: string; kind: string; decision: string; decided_by: string | null }[];

export function ApprovalsInbox() {
  const inbox = useQuery({
    queryKey: ["approvals", "inbox"],
    queryFn: () => apiGet<InboxItem[]>("/approvals/inbox"),
    refetchInterval: 15_000,
  });
  if (inbox.isPending) return <div className="h-40 animate-pulse rounded-card bg-card shadow-card" aria-busy="true" />;
  if (inbox.error) return <Card role="alert" className="text-rejected">Couldn&apos;t load approvals: {inbox.error.message}</Card>;
  if (inbox.data.length === 0) {
    return (
      <Card className="flex flex-col items-center gap-3 py-12 text-center">
        <span className="flex size-12 items-center justify-center rounded-control bg-approved-bg text-approved">
          <UserCheck aria-hidden className="size-6" />
        </span>
        <h2 className="text-lg font-semibold text-navy">No approvals waiting</h2>
        <p className="max-w-md text-muted">
          Cases come here when the approval matrix, a non-PO invoice, a bank change or an override needs a named
          approver.
        </p>
      </Card>
    );
  }
  return (
    <ul className="flex flex-col gap-4">
      {inbox.data.map((item) => (
        <InboxCard key={item.approval.id} item={item} />
      ))}
    </ul>
  );
}

function InboxCard({ item }: { item: InboxItem }) {
  const queryClient = useQueryClient();
  const { approval, run, request } = item;
  const nameId = useId();
  const commentId = useId();
  const [approver, setApprover] = useState(approval.approver_name ?? "");
  const [comment, setComment] = useState("");
  const decide = useMutation({
    mutationFn: (decision: "APPROVE" | "REJECT") =>
      apiPost<Run>(`/approvals/${approval.id}/decide`, { decision, approver, comment: comment || null }),
    onSuccess: () => {
      for (const key of [["approvals"], ["runs"], ["run", run.id], ["stats"]]) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    },
  });
  const chain = (request.approvals ?? []) as Chain;
  const involved = (request.already_involved ?? []) as string[];
  const invoice = (request.invoice ?? {}) as { number?: string; vendor?: string; total?: string; po?: string };
  const coding = request.proposed_coding as { gl_account: string; cost_centre: string; basis: string } | null;
  const decision = asDecision(run.decision);
  const busy = decide.isPending ? decide.variables : null;

  return (
    <li>
      <Card className="flex flex-col gap-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 flex-col gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <Link href={`/runs/${run.id}`} className="font-mono font-semibold text-navy hover:underline">
                {invoice.number ?? run.file_name}
              </Link>
              {decision && <StatusBadge decision={decision} />}
              <Pill tone="brand">{approval.kind.replace("_", " ")}</Pill>
              {approval.is_second_approver && (
                <Pill tone="hold">
                  <ShieldCheck aria-hidden className="size-3.5" /> second approver
                </Pill>
              )}
            </div>
            <span className="text-[13px] text-muted">
              {[invoice.vendor, invoice.total ? formatINR(invoice.total) : null, invoice.po, `waiting ${ago(run.created_at)}`]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </div>
          <Button asChild variant="ghost" size="sm">
            <Link href={`/runs/${run.id}`} aria-label={`Open case ${invoice.number ?? run.file_name}`}>
              <ExternalLink aria-hidden /> Open case
            </Link>
          </Button>
        </div>

        <div className="flex flex-col gap-1 rounded-control bg-canvas px-3.5 py-3">
          <span className="text-xs font-semibold text-brand-800">
            L{approval.level} · {approval.role}
            {approval.approver_name ? ` (${approval.approver_name})` : ""}
          </span>
          <span className="text-navy">{approval.reason}</span>
          {approval.citation && <CitationChip>{`[${approval.citation}]`}</CitationChip>}
          {coding && (
            <span className="text-[13px] text-ink">
              Proposed coding: GL {coding.gl_account} / {coding.cost_centre} — {coding.basis}
            </span>
          )}
        </div>

        <ol className="flex flex-wrap items-center gap-2" aria-label="Approval chain">
          {chain.map((c) => (
            <li key={c.id}>
              <Pill tone={c.decision === "APPROVED" ? "approved" : c.decision === "REJECTED" ? "rejected" : c.id === approval.id ? "review" : "neutral"}>
                L{c.level} {c.role}: {c.decision === "PENDING" ? (c.id === approval.id ? "now" : "next") : `${c.decision.toLowerCase()} by ${c.decided_by}`}
              </Pill>
            </li>
          ))}
        </ol>

        <div className="grid gap-3 sm:grid-cols-[minmax(0,240px)_1fr]">
          <label htmlFor={nameId} className="flex flex-col gap-1.5 text-[13px] font-medium text-navy">
            Approver
            <input id={nameId} value={approver} onChange={(e) => setApprover(e.target.value)}
              className="h-11 rounded-control border border-line bg-canvas px-3 text-sm font-normal" />
          </label>
          <label htmlFor={commentId} className="flex flex-col gap-1.5 text-[13px] font-medium text-navy">
            Reason / comment <span className="text-xs font-normal text-muted">(required to reject)</span>
            <input id={commentId} value={comment} onChange={(e) => setComment(e.target.value)}
              className="h-11 rounded-control border border-line bg-canvas px-3 text-sm font-normal" />
          </label>
        </div>
        {involved.length > 0 && (
          <p className="text-xs text-muted">
            Four-eyes: {involved.join(", ")} already acted on this case and can&apos;t approve it.
          </p>
        )}
        <div className="flex flex-wrap gap-2.5">
          <Button disabled={decide.isPending || !approver.trim()} onClick={() => decide.mutate("APPROVE")}>
            {busy === "APPROVE" ? <Loader2 aria-hidden className="animate-spin" /> : <Check aria-hidden />} Approve
          </Button>
          <Button variant="danger" disabled={decide.isPending || !approver.trim()} onClick={() => decide.mutate("REJECT")}>
            {busy === "REJECT" ? <Loader2 aria-hidden className="animate-spin" /> : <X aria-hidden />} Reject
          </Button>
        </div>
        {decide.error && (
          <p role="alert" className={cn("text-[13px] text-rejected")}>
            {decide.error.message}
          </p>
        )}
      </Card>
    </li>
  );
}
