"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Clock, Gauge, Inbox, Layers } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Pill, StatusBadge, type Decision } from "@/components/ui/badges";
import { Button } from "@/components/ui/button";
import { EvalCard } from "./eval-card";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { apiGet, type Run, type Stats } from "@/lib/api/client";
import { asDecision } from "@/lib/run-state";
import { cn, formatINR } from "@/lib/utils";
import { ago } from "../review/review-queue";

const RANGES = [
  { label: "7D", days: 7 },
  { label: "1M", days: 30 },
  { label: "3M", days: 90 },
  { label: "1Y", days: 365 },
] as const;

function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const mins = ms / 60_000;
  return mins < 60 ? `${mins.toFixed(1)} min` : `${(mins / 60).toFixed(1)} h`;
}

function shortDay(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

export function Dashboard() {
  const [range, setRange] = useState<(typeof RANGES)[number]>(RANGES[0]);
  const stats = useQuery({
    queryKey: ["stats", range.days],
    queryFn: () => apiGet<Stats>(`/stats?days=${range.days}`),
    refetchInterval: 20_000,
    placeholderData: (prev) => prev,
  });

  if (stats.isPending) {
    return (
      <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-4" aria-busy="true">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="h-36 animate-pulse rounded-card bg-card shadow-card" />
        ))}
      </div>
    );
  }
  if (stats.error) {
    return (
      <Card role="alert" className="flex items-center gap-2 text-rejected">
        <AlertTriangle aria-hidden className="size-4" /> Couldn&apos;t load statistics: {stats.error.message}
      </Card>
    );
  }
  const s = stats.data;
  if (s.runs === 0) {
    return (
      <Card className="flex flex-col items-center gap-3 py-12 text-center">
        <h2 className="text-lg font-semibold text-navy">No cases yet</h2>
        <p className="max-w-sm text-muted">Run a sample or upload an invoice and the numbers start here.</p>
        <Button asChild size="sm">
          <Link href="/">New run</Link>
        </Button>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi
          icon={<Gauge aria-hidden />}
          label="Straight-through rate"
          value={s.straight_through_rate === null || s.straight_through_rate === undefined ? "—" : `${Math.round(s.straight_through_rate * 100)}%`}
          note={`${s.straight_through} of ${s.completed} closed cases: approved, posted and verified with no human touch`}
        />
        <Kpi
          icon={<Inbox aria-hidden />}
          label="Waiting for a person"
          value={String(s.waiting_review + s.waiting_approval)}
          note={
            s.waiting_review + s.waiting_approval
              ? `${s.waiting_review} review · ${s.waiting_approval} approval · ${formatINR(s.pending_value)} · oldest ${
                  s.oldest_waiting_at ? ago(s.oldest_waiting_at) : "—"
                }`
              : "Nothing waiting"
          }
          href={s.waiting_review ? "/review" : s.waiting_approval ? "/approvals" : undefined}
        />
        <Kpi
          icon={<Clock aria-hidden />}
          label="Median cycle time"
          value={duration(s.median_touchless_ms)}
          note={`touchless cases · all closed cases ${duration(s.median_cycle_ms)} (includes review wait)`}
        />
        <Kpi
          icon={<Layers aria-hidden />}
          label="Cases processed"
          value={String(s.runs)}
          note={`${s.completed} closed · ${s.running} running · ${s.failed} failed`}
        />
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)] gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card>
          <CardHeader>
            <CardTitle>Processing volume</CardTitle>
            <div role="group" aria-label="Time range" className="flex gap-1 rounded-control bg-canvas p-1">
              {RANGES.map((r) => (
                <button
                  key={r.label}
                  type="button"
                  aria-pressed={range.label === r.label}
                  onClick={() => setRange(r)}
                  className={cn(
                    "h-9 min-w-11 rounded-[9px] px-3 text-[13px] font-medium",
                    range.label === r.label
                      ? "bg-gradient-to-br from-brand-650 to-brand-700 text-white"
                      : "text-muted hover:text-ink",
                  )}
                >
                  {r.label}
                </button>
              ))}
            </div>
          </CardHeader>
          <VolumeChart volume={s.volume} />
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Decisions</CardTitle>
          </CardHeader>
          <DecisionBars decisions={s.decisions} />
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Gate funnel</CardTitle>
            <span className="text-[13px] text-muted">where cases stop</span>
          </CardHeader>
          <Bars rows={s.funnel.map((f) => ({ label: f.stage, value: f.count }))} max={s.runs} showDrop />
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Exception causes</CardTitle>
            <span className="text-[13px] text-muted">{s.reviewed} cases reviewed by a person</span>
          </CardHeader>
          {Object.keys(s.exception_causes).length ? (
            <Bars
              rows={Object.entries(s.exception_causes).map(([label, value]) => ({ label, value, mono: true }))}
              max={Math.max(...Object.values(s.exception_causes))}
            />
          ) : (
            <p className="text-muted">No exceptions raised yet.</p>
          )}
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>ERP postings</CardTitle>
            <Link href="/erp" className="text-[13px] font-medium text-brand-700 hover:underline dark:text-brand-500">
              Ledger
            </Link>
          </CardHeader>
          <dl className="grid grid-cols-3 gap-3 text-center">
            {[
              ["Posted", s.erp_posted],
              ["Verified", s.erp_verified],
              ["Mismatch", s.erp_mismatches],
            ].map(([label, n]) => (
              <div key={label} className="rounded-control bg-canvas py-3">
                <dd className="text-2xl font-bold text-navy">{n}</dd>
                <dt className="text-xs text-muted">{label}</dt>
              </div>
            ))}
          </dl>
          <p className="mt-3 text-xs text-muted">{s.approvals_decided} approval decision(s) recorded.</p>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Exceptions by owner</CardTitle>
          </CardHeader>
          {s.exceptions_by_owner.length ? (
            <ul className="flex flex-col gap-2 text-[13px]">
              {s.exceptions_by_owner.map((o) => (
                <li key={o.owner} className="flex items-baseline justify-between gap-2">
                  <span>{o.owner}</span>
                  <span className="text-muted">
                    <b className="text-navy">{o.open}</b> open · {o.resolved} done
                    {o.median_resolution_ms ? ` · median ${duration(o.median_resolution_ms)}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted">No exceptions yet.</p>
          )}
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Corrections by vendor</CardTitle>
          </CardHeader>
          {Object.keys(s.corrections_by_vendor).length ? (
            <Bars
              rows={Object.entries(s.corrections_by_vendor).map(([label, value]) => ({ label, value, mono: true }))}
              max={Math.max(...Object.values(s.corrections_by_vendor))}
            />
          ) : (
            <p className="text-muted">No field corrections yet — they feed extraction hints for that vendor.</p>
          )}
        </Card>
      </div>

      <EvalCard />

      <RecentRuns runs={s.recent} />
    </div>
  );
}

function Kpi({
  icon,
  label,
  value,
  note,
  href,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  note: string;
  href?: string;
}) {
  const body = (
    <>
      <span className="flex items-center gap-2 text-[13px] font-medium text-muted [&_svg]:size-4 [&_svg]:text-brand-600 dark:[&_svg]:text-brand-500">
        {icon}
        {label}
      </span>
      <span className="text-[32px] leading-10 font-bold tracking-tight text-navy">{value}</span>
      <span className="text-xs leading-[18px] text-muted">{note}</span>
    </>
  );
  return (
    <Card className="flex flex-col gap-1.5">
      {href ? (
        <Link href={href} className="flex flex-col gap-1.5 rounded-control hover:opacity-90">
          {body}
        </Link>
      ) : (
        body
      )}
    </Card>
  );
}

type DayPoint = Stats["volume"][number];

function VolumeTooltip({ active, payload }: { active?: boolean; payload?: { payload: DayPoint }[] }) {
  const p = payload?.[0]?.payload;
  if (!active || !p) return null;
  return (
    <div className="rounded-control bg-card px-3 py-2 text-[13px] shadow-card">
      <div className="font-semibold text-navy">
        {shortDay(p.day)} · {p.total} case{p.total === 1 ? "" : "s"}
      </div>
      <div className="text-muted">
        {p.approved} approved · {p.needs_review} review · {p.on_hold} hold · {p.rejected} rejected
      </div>
    </div>
  );
}

function VolumeChart({ volume }: { volume: Stats["volume"] }) {
  const total = volume.reduce((n, d) => n + d.total, 0);
  return (
    <figure className="flex flex-col gap-2">
      <div className="h-60" aria-hidden>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={volume} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
            <defs>
              <linearGradient id="volumeFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--color-chart)" stopOpacity={0.22} />
                <stop offset="100%" stopColor="var(--color-chart)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} horizontal={false} />
            <XAxis
              dataKey="day"
              tickFormatter={shortDay}
              tick={{ fill: "var(--color-muted)", fontSize: 12 }}
              axisLine={{ stroke: "var(--color-line)" }}
              tickLine={false}
              minTickGap={24}
            />
            <YAxis
              allowDecimals={false}
              tick={{ fill: "var(--color-muted)", fontSize: 12 }}
              axisLine={false}
              tickLine={false}
              width={44}
            />
            <Tooltip content={<VolumeTooltip />} cursor={{ stroke: "var(--color-muted)", strokeDasharray: "3 3" }} />
            <Area
              type="monotone"
              dataKey="total"
              stroke="var(--color-chart)"
              strokeWidth={2.5}
              fill="url(#volumeFill)"
              activeDot={{ r: 5, stroke: "var(--color-card)", strokeWidth: 2, fill: "var(--color-chart)" }}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="text-xs text-muted">
        {total} case{total === 1 ? "" : "s"} received in this period, per day.
      </figcaption>
      {/* sr-only on the wrapper: a <table> ignores the 1px width and would widen the page */}
      <div className="sr-only">
      <table>
        <caption>Cases received per day</caption>
        <thead>
          <tr>
            <th>Day</th>
            <th>Cases</th>
            <th>Approved</th>
            <th>Needs review</th>
            <th>On hold</th>
            <th>Rejected</th>
          </tr>
        </thead>
        <tbody>
          {volume
            .filter((d) => d.total > 0)
            .map((d) => (
              <tr key={d.day}>
                <td>{d.day}</td>
                <td>{d.total}</td>
                <td>{d.approved}</td>
                <td>{d.needs_review}</td>
                <td>{d.on_hold}</td>
                <td>{d.rejected}</td>
              </tr>
            ))}
        </tbody>
      </table>
      </div>
    </figure>
  );
}

const DECISION_ORDER: Decision[] = ["APPROVED", "NEEDS_REVIEW", "ON_HOLD", "REJECTED"];
const DECISION_FILL: Record<Decision, string> = {
  APPROVED: "bg-approved",
  NEEDS_REVIEW: "bg-review",
  ON_HOLD: "bg-hold",
  REJECTED: "bg-rejected",
};

function DecisionBars({ decisions }: { decisions: Record<string, number> }) {
  const max = Math.max(1, ...Object.values(decisions));
  return (
    <ul className="flex flex-col gap-4">
      {DECISION_ORDER.map((d) => {
        const n = decisions[d] ?? 0;
        return (
          <li key={d} className="flex flex-col gap-1.5">
            <div className="flex items-center justify-between">
              <StatusBadge decision={d} />
              <span className="font-semibold text-navy">{n}</span>
            </div>
            <div className="h-2 rounded-full bg-canvas" aria-hidden>
              <div className={cn("h-2 rounded-full", DECISION_FILL[d])} style={{ width: `${(n / max) * 100}%` }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function Bars({
  rows,
  max,
  showDrop,
}: {
  rows: { label: string; value: number; mono?: boolean }[];
  max: number;
  showDrop?: boolean;
}) {
  return (
    <ul className="flex flex-col gap-3">
      {rows.map((r, i) => {
        const prev = rows[i - 1]?.value;
        const drop = showDrop && prev !== undefined && prev > r.value ? prev - r.value : 0;
        return (
          <li key={r.label} className="flex flex-col gap-1">
            <div className="flex items-baseline justify-between gap-2 text-[13px]">
              <span className={cn("text-ink", r.mono && "font-mono text-xs")}>{r.label}</span>
              <span className="text-muted">
                <span className="font-semibold text-navy">{r.value}</span>
                {drop > 0 && <span className="ml-1.5 text-xs">−{drop}</span>}
              </span>
            </div>
            <div className="h-2 rounded-full bg-canvas" aria-hidden>
              <div
                className="h-2 rounded-r-[4px] rounded-l-full bg-chart"
                style={{ width: `${max ? (r.value / max) * 100 : 0}%` }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

const RUN_STATUS_LABEL: Record<string, string> = {
  QUEUED: "Queued",
  RUNNING: "Running",
  WAITING_REVIEW: "Waiting",
  WAITING_APPROVAL: "Approval",
  FAILED: "Failed",
};

function RecentRuns({ runs }: { runs: Run[] }) {
  return (
    <Card className="p-0 sm:p-0">
      <div className="px-[22px] pt-[22px] sm:px-6 sm:pt-6">
        <CardHeader className="mb-2">
          <CardTitle>Recent runs</CardTitle>
          <Button asChild variant="soft" size="sm">
            <Link href="/">New run</Link>
          </Button>
        </CardHeader>
      </div>
      <div className="overflow-x-auto px-2 pb-2">
        <table className="w-full min-w-[720px] text-sm">
          <thead>
            <tr className="text-left text-xs text-muted">
              <th className="px-4 py-3 font-medium">Invoice</th>
              <th className="px-4 py-3 font-medium">Vendor</th>
              <th className="px-4 py-3 text-right font-medium">Amount</th>
              <th className="px-4 py-3 font-medium">Decision</th>
              <th className="px-4 py-3 font-medium">Received</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => {
              const d = asDecision(r.final_decision ?? r.decision);
              const done = r.status === "COMPLETED";
              return (
                <tr key={r.id} className="border-t border-line">
                  <td className="px-4 py-3">
                    <Link href={`/runs/${r.id}`} className="font-mono text-[13px] font-medium text-navy hover:underline">
                      {r.invoice_number ?? r.file_name}
                    </Link>
                  </td>
                  <td className="px-4 py-3">{r.vendor_name ?? "—"}</td>
                  <td className="px-4 py-3 text-right font-mono text-[13px]">{r.total ? formatINR(r.total) : "—"}</td>
                  <td className="px-4 py-3">
                    {done && d ? (
                      <StatusBadge decision={d} />
                    ) : (
                      <Pill tone={r.status === "FAILED" ? "rejected" : "review"}>
                        {RUN_STATUS_LABEL[r.status] ?? r.status}
                      </Pill>
                    )}
                  </td>
                  <td className="px-4 py-3 text-muted">{ago(r.created_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
