"use client";

import { useQuery } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import { Pill } from "@/components/ui/badges";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, apiGet, type EvalLatest } from "@/lib/api/client";

function pct(v: unknown): string {
  return typeof v === "number" ? `${(v * 100).toFixed(v === 1 ? 0 : 1)}%` : "—";
}

function num(v: unknown, unit = ""): string {
  return typeof v === "number" ? `${v.toLocaleString("en-IN")}${unit}` : "—";
}

/** Latest offline evaluation (scripts/eval.py → data/eval/latest.json); not live traffic. */
export function EvalCard() {
  const q = useQuery({
    queryKey: ["eval-latest"],
    queryFn: () => apiGet<EvalLatest>("/eval/latest"),
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 2,
  });
  if (q.isPending) return <div className="h-40 animate-pulse rounded-card bg-card shadow-card" aria-busy="true" />;
  if (q.error) {
    if (q.error instanceof ApiError && q.error.status === 404) return null;
    return null;
  }
  const s = q.data.summary;
  const rag = q.data.rag ?? {};
  const items: { label: string; value: string; note: string }[] = [
    { label: "Decision accuracy", value: pct(s.decision_accuracy), note: `${s.cases} sample invoices vs ground truth` },
    {
      label: "Extraction accuracy",
      value: pct(s.field_accuracy),
      note: `${num(s.fields_scored)} fields · ${s.cases_all_fields_right}/${s.cases} invoices perfect`,
    },
    { label: "Retrieval hit-rate@5", value: pct(rag.hit_rate_at_5), note: `${num(rag.questions)} policy & contract questions` },
    { label: "Answer correctness", value: pct(rag.answer_correctness), note: "LLM judge, facts covered" },
    {
      label: "Time to decision",
      value: num(s.median_seconds, " s"),
      note: `median · p90 ${num(s.p90_seconds, " s")}`,
    },
    {
      label: "Tokens per invoice",
      value: num(s.tokens_per_invoice),
      note: `${num(s.llm_calls_per_invoice)} LLM calls`,
    },
  ];
  const when = new Date(q.data.run_at).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FlaskConical aria-hidden className="size-4 text-brand-600 dark:text-brand-500" />
          Evaluation
        </CardTitle>
        <span className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <Pill tone={q.data.live ? "brand" : "neutral"}>{q.data.live ? "live LLM run" : "replayed outputs"}</Pill>
          {when} · {q.data.models.join(", ") || "—"}
        </span>
      </CardHeader>
      <dl className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {items.map((it) => (
          <div key={it.label} className="flex flex-col gap-0.5 rounded-control bg-canvas px-3.5 py-3">
            <dt className="text-xs font-medium text-muted">{it.label}</dt>
            <dd className="text-2xl font-bold text-navy">{it.value}</dd>
            <dd className="text-xs text-muted">{it.note}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-xs text-muted">
        From <code className="font-mono">python -m scripts.eval --live --rag --write</code> on the 25 sample invoices
        — details in <code className="font-mono">docs/EVAL_RESULTS.md</code>.
      </p>
    </Card>
  );
}
