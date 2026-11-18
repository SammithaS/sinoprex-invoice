"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, FileDown, FileJson } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import type { ReactNode } from "react";
import { CitationChip, Pill, StatusBadge } from "@/components/ui/badges";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { apiGet, backendUrl } from "@/lib/api/client";
import { asDecision } from "@/lib/run-state";
import { formatINR } from "@/lib/utils";

/* eslint-disable @typescript-eslint/no-explicit-any -- the bundle is a JSON document */
type Bundle = Record<string, any>;

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card className="flex flex-col gap-3">
      <CardTitle>{title}</CardTitle>
      {children}
    </Card>
  );
}

function when(iso: string | null | undefined) {
  return iso ? new Date(`${iso}Z`).toLocaleString("en-IN") : "—";
}

export function EvidenceView() {
  const { id } = useParams<{ id: string }>();
  const q = useQuery({ queryKey: ["evidence", id], queryFn: () => apiGet<Bundle>(`/cases/${id}/evidence`) });
  if (q.isPending) return <div className="h-96 animate-pulse rounded-card bg-card shadow-card" aria-busy="true" />;
  if (q.error) return <Card role="alert" className="text-rejected">Couldn&apos;t load the evidence: {q.error.message}</Card>;
  const b = q.data;
  const c = b.case;
  const final = asDecision(c.final_decision ?? c.decision_by_rules);

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <Link href={`/runs/${id}`} className="inline-flex min-h-6 items-center gap-1 text-[13px] font-medium text-brand-700 dark:text-brand-500">
            <ChevronLeft aria-hidden className="size-3.5" /> Back to the case
          </Link>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="font-mono text-[26px] leading-9 font-semibold text-navy">{c.invoice_number ?? c.file_name}</h1>
            {final && <StatusBadge decision={final} />}
            <Pill>{c.status}</Pill>
          </div>
          <span className="text-muted">
            {[c.vendor?.name, c.total ? formatINR(c.total) : null, `procedure ${b.procedure.version}`].filter(Boolean).join(" · ")}
          </span>
        </div>
        <div className="flex flex-wrap gap-2.5">
          <Button asChild variant="ghost">
            <a href={backendUrl(`/cases/${id}/evidence.json`)}>
              <FileJson aria-hidden /> JSON
            </a>
          </Button>
          <Button asChild>
            <a href={backendUrl(`/cases/${id}/evidence.pdf`)}>
              <FileDown aria-hidden /> PDF
            </a>
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-5 xl:grid-cols-2">
        <Section title="Case">
          <dl className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-1.5 text-[13px]">
            <dt className="text-muted">Received</dt>
            <dd>{when(c.received_at)}</dd>
            <dt className="text-muted">Closed</dt>
            <dd>{when(c.closed_at)}</dd>
            <dt className="text-muted">File SHA-256</dt>
            <dd className="font-mono break-all">{c.file_sha256}</dd>
            <dt className="text-muted">Rules decided</dt>
            <dd>{c.decision_by_rules}</dd>
            <dt className="text-muted">Final decision</dt>
            <dd>{c.final_decision ?? "open"}</dd>
            <dt className="text-muted">Extraction model</dt>
            <dd>{b.extraction.model ?? "—"}</dd>
          </dl>
          {b.explanation.summary && <p className="leading-6">{b.explanation.summary}</p>}
        </Section>

        <Section title="Exceptions">
          {b.exceptions.length === 0 && <p className="text-muted">None.</p>}
          {b.exceptions.map((e: Bundle, i: number) => (
            <div key={i} className="flex flex-col gap-1.5 rounded-control border border-line p-3 text-[13px]">
              <div className="flex flex-wrap items-center gap-2">
                <Pill className="font-mono">{e.category}</Pill>
                <Pill tone={e.status === "OPEN" ? "review" : e.status === "REJECTED" ? "rejected" : "approved"}>{e.status}</Pill>
                <span className="text-muted">→ {e.owner}</span>
              </div>
              <span>{e.summary}</span>
              <span className="font-medium text-navy">Q: {e.question}</span>
              {(e.attempted_steps ?? []).map((s: string) => (
                <span key={s} className="text-xs text-muted">Tried: {s}</span>
              ))}
              {e.resolved_by && (
                <span className="text-xs">
                  {e.status.toLowerCase()} by {e.resolved_by} at {when(e.resolved_at)}
                  {e.resolution ? ` — “${e.resolution}”` : ""}
                </span>
              )}
            </div>
          ))}
        </Section>

        <Section title="Reviews & approvals">
          {b.reviews.length === 0 && b.approvals.length === 0 && <p className="text-muted">No human action needed.</p>}
          <ol className="flex flex-col gap-2 text-[13px]">
            {b.reviews.map((r: Bundle, i: number) => (
              <li key={`r${i}`}>
                <span className="text-muted">{when(r.at)}</span> · Review by <b>{r.reviewer ?? "—"}</b>: {r.action}
                {r.comment ? ` — “${r.comment}”` : ""}
              </li>
            ))}
            {b.approvals.map((a: Bundle) => (
              <li key={`a${a.level}`}>
                <span className="text-muted">{when(a.at)}</span> · L{a.level} {a.role} ({a.kind.replace("_", " ").toLowerCase()}
                {a.second_approver ? ", second approver" : ""}): <b>{a.decision}</b>
                {a.decided_by ? ` by ${a.decided_by}` : ""}
                {a.comment ? ` — “${a.comment}”` : ""} <CitationChip>{`[${a.citation}]`}</CitationChip>
              </li>
            ))}
          </ol>
        </Section>

        <Section title="ERP posting">
          {!b.erp && <p className="text-muted">Not posted.</p>}
          {b.erp && (
            <div className="flex flex-col gap-1.5 text-[13px]">
              <span>
                Document <span className="font-mono font-semibold">{b.erp.doc_number}</span> · read-back{" "}
                {b.erp.verified ? <Pill tone="approved">verified</Pill> : <Pill tone="rejected">mismatch</Pill>}
              </span>
              <span className="font-mono text-xs">
                {formatINR(b.erp.request.amount)} → GL {b.erp.request.gl_account} / {b.erp.request.cost_centre}
              </span>
              {(b.erp.mismatches ?? []).map((m: string) => (
                <span key={m} className="text-rejected">{m}</span>
              ))}
            </div>
          )}
        </Section>

        <Section title="Rule checks">
          <ul className="flex flex-col gap-1 text-[13px]">
            {(b.rules.results ?? []).map((r: Bundle) => (
              <li key={r.code}>
                <span className="font-mono text-xs">{r.status}</span> {r.name}
                {r.status !== "PASS" && <span className="text-muted"> — {r.message}</span>}
              </li>
            ))}
          </ul>
        </Section>

        <Section title="Corrections">
          {b.corrections.length === 0 && <p className="text-muted">None.</p>}
          {b.corrections.map((x: Bundle, i: number) => (
            <p key={i} className="text-[13px]">
              <span className="font-mono">{x.field}</span>: {x.old ?? "—"} → <b>{x.new}</b> by {x.by ?? "—"}
            </p>
          ))}
        </Section>
      </div>

      <Section title="Timeline">
        <ol className="flex flex-col gap-1 text-[13px]">
          {b.timeline
            .filter((t: Bundle) => t.type !== "step_started")
            .map((t: Bundle) => (
              <li key={t.seq} className="grid grid-cols-[minmax(0,1fr)] gap-x-2 gap-y-0.5 sm:grid-cols-[160px_150px_minmax(0,1fr)]">
                <span className="text-muted">{when(t.at)}</span>
                <span className="font-mono text-xs">{t.node}</span>
                <span>{t.message}</span>
              </li>
            ))}
        </ol>
      </Section>
    </div>
  );
}
