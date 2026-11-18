"use client";

import { CheckCircle2, ShieldCheck } from "lucide-react";
import { CitationChip, Pill } from "@/components/ui/badges";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import type { CaseRecords } from "@/lib/api/client";
import { formatINR } from "@/lib/utils";

const STATUS_TONE = {
  OPEN: "review",
  OVERRIDDEN: "hold",
  REJECTED: "rejected",
  RESOLVED: "approved",
} as const;

export function ExceptionsCard({ records }: { records: CaseRecords }) {
  if (records.exceptions.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Exceptions</CardTitle>
        <span className="text-[13px] text-muted">{records.exceptions.filter((e) => e.status === "OPEN").length} open</span>
      </CardHeader>
      <ul className="flex flex-col gap-3">
        {records.exceptions.map((e) => (
          <li key={e.id} className="flex flex-col gap-1.5 rounded-control border border-line p-3">
            <div className="flex flex-wrap items-center gap-2">
              <Pill className="font-mono">{e.category}</Pill>
              <Pill tone={STATUS_TONE[e.status as keyof typeof STATUS_TONE] ?? "neutral"}>{e.status.toLowerCase()}</Pill>
              <span className="text-[13px] text-muted">
                → {e.owner_role}
                {e.owner_name ? ` (${e.owner_name})` : ""}
              </span>
            </div>
            <span className="text-[13px]">{e.summary}</span>
            <span className="text-[13px] font-medium text-navy">{e.question}</span>
            {(e.attempted_steps ?? []).map((s) => (
              <span key={s} className="text-xs text-muted">
                Already tried: {s}
              </span>
            ))}
            {e.recommendation && <span className="text-xs text-ink">Recommendation: {e.recommendation}</span>}
            {e.resolved_by && (
              <span className="text-xs text-muted">
                {e.status.toLowerCase()} by {e.resolved_by}
                {e.resolution ? ` — “${e.resolution}”` : ""}
              </span>
            )}
            {(e.citations ?? []).length > 0 && (
              <span className="flex flex-wrap gap-1.5">
                {(e.citations ?? []).map((c) => (
                  <CitationChip key={c}>{`[${c}]`}</CitationChip>
                ))}
              </span>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function ApprovalsCard({ records }: { records: CaseRecords }) {
  if (records.approvals.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Approvals</CardTitle>
        <span className="text-[13px] text-muted">
          {records.approvals.filter((a) => a.decision === "APPROVED").length} of {records.approvals.length} approved
        </span>
      </CardHeader>
      <ol className="flex flex-col gap-2.5">
        {records.approvals.map((a) => (
          <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-2.5 first:border-t-0 first:pt-0">
            <div className="flex min-w-0 flex-col">
              <span className="font-medium text-navy">
                L{a.level} · {a.role}
                {a.is_second_approver && <ShieldCheck aria-label="second approver" className="ml-1 inline size-3.5 text-hold" />}
              </span>
              <span className="text-xs text-muted">{a.reason}</span>
            </div>
            <Pill tone={a.decision === "APPROVED" ? "approved" : a.decision === "REJECTED" ? "rejected" : "review"}>
              {a.decision === "PENDING" ? `waiting for ${a.approver_name ?? a.role}` : `${a.decision.toLowerCase()} by ${a.decided_by}`}
            </Pill>
          </li>
        ))}
      </ol>
    </Card>
  );
}

export function ErpCard({ records }: { records: CaseRecords }) {
  const p = records.erp;
  if (!p) return null;
  const req = p.request as { amount?: string; gl_account?: string; cost_centre?: string; po_number?: string | null };
  return (
    <Card>
      <CardHeader>
        <CardTitle>ERP posting</CardTitle>
        {p.verified ? (
          <Pill tone="approved">
            <CheckCircle2 aria-hidden className="size-3.5" /> Read-back verified
          </Pill>
        ) : (
          <Pill tone="rejected">Read-back mismatch</Pill>
        )}
      </CardHeader>
      <dl className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-1.5 text-[13px]">
        <dt className="text-muted">Document</dt>
        <dd className="font-mono">{p.doc_number}</dd>
        <dt className="text-muted">Amount</dt>
        <dd className="font-mono">{req.amount ? formatINR(req.amount) : "—"}</dd>
        <dt className="text-muted">Coding</dt>
        <dd className="font-mono">
          GL {req.gl_account ?? "—"} / {req.cost_centre ?? "—"}
        </dd>
        <dt className="text-muted">PO</dt>
        <dd className="font-mono">{req.po_number ?? "non-PO"}</dd>
      </dl>
      {(p.mismatches ?? []).length > 0 && (
        <ul className="mt-2 text-[13px] text-rejected">
          {(p.mismatches ?? []).map((m) => (
            <li key={m}>{m}</li>
          ))}
        </ul>
      )}
    </Card>
  );
}
