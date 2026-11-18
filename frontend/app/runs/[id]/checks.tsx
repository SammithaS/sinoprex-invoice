"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert, Minus, Pencil, X } from "lucide-react";
import { useState } from "react";
import { CitationChip, ConfidenceChip } from "@/components/ui/badges";
import { Button } from "@/components/ui/button";
import { apiPost } from "@/lib/api/client";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import {
  FIELD_LABELS,
  TOUCHLESS_LABELS,
  type Assessment,
  type Clause,
  type Extraction,
  type RuleStatus,
} from "@/lib/run-state";
import { cn, formatINR, formatQty } from "@/lib/utils";

const RULE_ICON: Record<RuleStatus, { icon: typeof Check; cls: string; label: string }> = {
  PASS: { icon: Check, cls: "bg-approved-bg text-approved", label: "Pass" },
  WARN: { icon: CircleAlert, cls: "bg-review-bg text-review", label: "Warning" },
  FAIL: { icon: X, cls: "bg-rejected-bg text-rejected", label: "Fail" },
  SKIP: { icon: Minus, cls: "bg-canvas text-muted", label: "Not applicable" },
};

export function RuleRow({
  status,
  name,
  message,
  citations,
  compact,
}: {
  status: RuleStatus;
  name: string;
  message: string;
  citations: string[];
  compact?: boolean;
}) {
  const { icon: Icon, cls, label } = RULE_ICON[status];
  return (
    <li className="flex gap-2.5">
      <span className={cn("mt-0.5 flex size-[22px] shrink-0 items-center justify-center rounded-full", cls)}>
        <Icon aria-hidden className="size-3" strokeWidth={3} />
        <span className="sr-only">{label}</span>
      </span>
      <div className="flex min-w-0 flex-col gap-1">
        <span className={cn("font-medium text-navy", compact && "text-[13px]")}>{name}</span>
        {(!compact || status !== "PASS") && (
          <span className={cn("text-muted", compact ? "text-xs" : "text-[13px]")}>{message}</span>
        )}
        {!compact && citations.length > 0 && (
          <span className="flex flex-wrap gap-1.5">
            {citations.map((c) => (
              <CitationChip key={c}>{`[${c}]`}</CitationChip>
            ))}
          </span>
        )}
      </div>
    </li>
  );
}

export function TouchlessCard({ assessment, erpVerified }: { assessment: Assessment; erpVerified: boolean | null }) {
  const items: [string, boolean][] = [...Object.entries(assessment.touchless), ["erp_verified", erpVerified === true]];
  const passed = items.filter(([, ok]) => ok).length;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Touchless checklist</CardTitle>
        <span className="text-[13px] text-muted">
          {passed} of {items.length} passed
        </span>
      </CardHeader>
      <ul className="flex flex-col gap-2.5">
        {items.map(([key, ok]) => (
          <li key={key} className="flex items-center gap-2.5">
            <span
              className={cn(
                "flex size-[22px] shrink-0 items-center justify-center rounded-full",
                ok ? "bg-approved-bg text-approved" : "bg-review-bg text-review",
              )}
            >
              {ok ? (
                <Check aria-hidden className="size-3" strokeWidth={3} />
              ) : (
                <CircleAlert aria-hidden className="size-3" strokeWidth={3} />
              )}
              <span className="sr-only">{ok ? "Passed" : "Not met"}</span>
            </span>
            <span>{TOUCHLESS_LABELS[key] ?? key}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function RulesCard({ assessment }: { assessment: Assessment }) {
  const order: RuleStatus[] = ["FAIL", "WARN", "PASS", "SKIP"];
  const rules = [...assessment.rule_results].sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status));
  return (
    <Card>
      <CardHeader>
        <CardTitle>Rule checks</CardTitle>
        <span className="text-[13px] text-muted">
          {rules.filter((r) => r.status === "PASS").length} pass ·{" "}
          {rules.filter((r) => r.status === "WARN" || r.status === "FAIL").length} flagged
        </span>
      </CardHeader>
      <ul className="flex flex-col gap-3.5">
        {rules.map((r) => (
          <RuleRow key={r.code} status={r.status} name={r.name} message={r.message} citations={r.citations} />
        ))}
      </ul>
    </Card>
  );
}

export function ClausesCard({ clauses, cited }: { clauses: Clause[]; cited: string[] }) {
  const used = clauses.filter((c) => cited.includes(c.citation));
  const policy = cited.filter((c) => !clauses.some((x) => x.citation === c));
  if (used.length === 0 && policy.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Cited sources</CardTitle>
      </CardHeader>
      <div className="flex flex-col gap-3">
        {used.map((c) => (
          <blockquote key={c.citation} className="flex flex-col gap-1.5 rounded-control bg-canvas p-3">
            <CitationChip>{`[${c.citation}]`}</CitationChip>
            <p className="text-[13px] leading-5 whitespace-pre-line text-ink">
              {c.text.split("\n").slice(1).join("\n").replace(/\*\*/g, "") || c.text}
            </p>
          </blockquote>
        ))}
        {policy.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {policy.map((c) => (
              <CitationChip key={c}>{`[${c}]`}</CitationChip>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}

function money(field: string, value: string | null): string {
  if (value === null) return "—";
  return ["subtotal", "tax_total", "total"].includes(field) ? formatINR(value) : value;
}

function CorrectField({ runId, field, label }: { runId: string; field: string; label: string }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const save = useMutation({
    mutationFn: () => apiPost(`/runs/${runId}/corrections`, { field, new_value: value, reviewer: "Priya Nair" }),
    onSuccess: () => {
      setOpen(false);
      setValue("");
      void queryClient.invalidateQueries({ queryKey: ["records", runId] });
    },
  });
  if (!open) {
    return (
      <Button variant="icon" size="icon" className="size-8" aria-label={`Correct ${label}`} onClick={() => setOpen(true)}>
        <Pencil aria-hidden className="size-3.5!" />
      </Button>
    );
  }
  return (
    <form
      className="flex items-center gap-1.5"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) save.mutate();
      }}
    >
      <input
        autoFocus
        aria-label={`Correct value for ${label}`}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="h-8 w-36 rounded-lg border border-line bg-canvas px-2 font-mono text-xs"
      />
      <Button size="sm" type="submit" className="h-8 px-2.5" disabled={save.isPending || !value.trim()}>
        Save
      </Button>
    </form>
  );
}

export function FieldsCard({
  extraction,
  runId,
  editable,
  corrected,
}: {
  extraction: Extraction;
  runId?: string;
  editable?: boolean;
  corrected?: Record<string, string>;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Extracted fields</CardTitle>
        <span className="text-[13px] text-muted">{extraction.model ?? ""}</span>
      </CardHeader>
      <dl className="flex flex-col">
        {FIELD_LABELS.map(([key, label]) => {
          const ev = extraction.evidence[key];
          if (!ev) return null;
          return (
            <div key={key} className="flex items-center gap-3 border-t border-line py-2 first:border-t-0">
              <dt className="w-28 shrink-0 text-[13px] text-muted">{label}</dt>
              <dd className="min-w-0 flex-1 truncate font-mono text-[13px] text-navy" title={ev.snippet ?? undefined}>
                {money(key, ev.value)}
              </dd>
              {corrected?.[key] ? (
                <span className="font-mono text-xs text-approved" title="Corrected by a reviewer">
                  → {corrected[key]}
                </span>
              ) : (
                ev.value !== null && <ConfidenceChip value={ev.confidence} />
              )}
              {editable && runId && <CorrectField runId={runId} field={key} label={label} />}
            </div>
          );
        })}
      </dl>
      {extraction.data.lines.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[420px] text-[13px]">
            <thead>
              <tr className="text-left text-xs text-muted">
                <th className="py-1.5 pr-2 font-medium">Item</th>
                <th className="py-1.5 pr-2 text-right font-medium">Qty</th>
                <th className="py-1.5 pr-2 text-right font-medium">Rate</th>
                <th className="py-1.5 text-right font-medium">Amount</th>
              </tr>
            </thead>
            <tbody>
              {extraction.data.lines.map((l) => (
                <tr key={l.line_no} className="border-t border-line">
                  <td className="py-1.5 pr-2">{l.description}</td>
                  <td className="py-1.5 pr-2 text-right font-mono">{l.qty ? formatQty(l.qty) : "—"}</td>
                  <td className="py-1.5 pr-2 text-right font-mono">{l.unit_price ? formatINR(l.unit_price) : "—"}</td>
                  <td className="py-1.5 text-right font-mono">{l.amount ? formatINR(l.amount) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
