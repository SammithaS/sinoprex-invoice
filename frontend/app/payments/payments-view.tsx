"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  Landmark,
  Loader2,
  PauseCircle,
  RotateCcw,
  Send,
  ShieldCheck,
  X,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Pill } from "@/components/ui/badges";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { appsCall } from "@/lib/api/apps";
import { apiGet, type PayableItem, type PaymentBatch, type PaymentsOverview } from "@/lib/api/client";
import { cn, formatDate, formatINR } from "@/lib/utils";

const STATES = {
  DUE: { label: "Due", tone: "review" },
  PROPOSED: { label: "Proposed", tone: "brand" },
  HELD: { label: "On hold", tone: "hold" },
  FAILED: { label: "Failed", tone: "rejected" },
  IN_FLIGHT: { label: "In flight", tone: "brand" },
  PAID: { label: "Paid", tone: "approved" },
  SCHEDULED: { label: "Not due yet", tone: "neutral" },
} as const;

type StateKey = keyof typeof STATES;
const FILTERS: { key: "ALL" | StateKey; label: string }[] = [
  { key: "ALL", label: "All" },
  { key: "DUE", label: "Due" },
  { key: "HELD", label: "On hold" },
  { key: "FAILED", label: "Failed" },
  { key: "IN_FLIGHT", label: "In flight" },
  { key: "PAID", label: "Paid" },
  { key: "SCHEDULED", label: "Not due yet" },
];

const DAY_NAMES: Record<string, string> = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri" };

function sum(items: PayableItem[]): number {
  return items.reduce((total, i) => total + Number(i.amount), 0);
}

export function PaymentsView() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<"ALL" | StateKey>("ALL");
  const overview = useQuery({
    queryKey: ["payments"],
    queryFn: () => apiGet<PaymentsOverview>("/payments"),
    refetchInterval: 15_000,
  });
  const refresh = () => {
    for (const key of [["payments"], ["erp"], ["stats"]]) void queryClient.invalidateQueries({ queryKey: key });
  };
  const propose = useMutation({
    mutationFn: () => appsCall<{ status: string; message: string | null }>("POST", "/payments/propose"),
    onSettled: refresh,
  });

  if (overview.isPending) return <div className="h-60 animate-pulse rounded-card bg-card shadow-card" aria-busy="true" />;
  if (overview.error) {
    return <Card role="alert" className="text-rejected">Couldn&apos;t load payments: {overview.error.message}</Card>;
  }
  const data = overview.data;
  const by = (s: StateKey) => data.queue.filter((i) => i.state === s);
  const due = by("DUE");
  const proposed = data.batches.filter((b) => b.status === "PROPOSED");
  const released = data.batches.filter((b) => b.status === "RELEASED");
  const shown = filter === "ALL" ? data.queue : by(filter);
  const days = data.payment_days.map((d) => DAY_NAMES[d] ?? d).join(" & ");

  return (
    <div className="flex flex-col gap-6">
      {!data.app_active && (
        <Card className="flex flex-wrap items-center gap-3 border border-hold/30 bg-hold-bg/40">
          <AlertTriangle aria-hidden className="size-5 text-hold" />
          <p className="flex-1 text-sm">
            Payouts are off. Install and activate <strong>RazorpayX Payouts</strong> (test keys move no real money) to
            propose and release payment runs.
          </p>
          <Button asChild size="sm" variant="soft">
            <Link href="/apps/razorpayx-payouts">Set up RazorpayX</Link>
          </Button>
        </Card>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Tile icon={CalendarClock} label="Next payment day" value={formatDate(data.next_payment_day)} note={`Runs on ${days}`} />
        <Tile icon={Send} label="Due in the next run" value={formatINR(sum(due))} note={`${due.length} invoice(s)`} />
        <Tile icon={PauseCircle} label="On hold or failed" value={String(by("HELD").length + by("FAILED").length)} note="need a person to look" />
        <Tile
          icon={CheckCircle2}
          label="Paid"
          value={formatINR(sum(by("PAID")))}
          note={`${by("PAID").length} invoice(s) · ${by("IN_FLIGHT").length} in flight`}
        />
      </div>

      <Card className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-3">
          <CardTitle className="flex-1">Payment run</CardTitle>
          <Button
            size="sm"
            disabled={!data.app_active || propose.isPending || due.length === 0}
            onClick={() => propose.mutate()}
          >
            {propose.isPending ? <Loader2 aria-hidden className="animate-spin" /> : <Landmark aria-hidden />} Propose a
            run now
          </Button>
        </div>
        {propose.data?.message && <p className="text-sm text-muted">{propose.data.message}</p>}
        {propose.error && <p className="text-sm text-rejected">{propose.error.message}</p>}
        {proposed.length === 0 ? (
          <p className="text-sm text-muted">
            No batch waiting for release. On each payment day the RazorpayX Payouts app proposes one automatically.
          </p>
        ) : (
          proposed.map((b) => (
            <ProposedBatch
              key={b.batch_id}
              batch={b}
              queue={data.queue}
              onDone={() => {
                propose.reset();
                refresh();
              }}
            />
          ))
        )}
      </Card>

      <Card className="p-0 sm:p-0">
        <div className="flex flex-wrap gap-2 px-[22px] pt-[22px] sm:px-6 sm:pt-6" role="tablist" aria-label="Filter">
          {FILTERS.map((f) => {
            const count = f.key === "ALL" ? data.queue.length : by(f.key).length;
            return (
              <button
                key={f.key}
                role="tab"
                aria-selected={filter === f.key}
                onClick={() => setFilter(f.key)}
                className={cn(
                  "rounded-full px-3 py-1 text-[13px] font-medium",
                  filter === f.key ? "bg-brand-50 text-brand-800" : "text-muted hover:bg-canvas",
                )}
              >
                {f.label} <span className="font-mono text-xs">{count}</span>
              </button>
            );
          })}
        </div>
        {shown.length === 0 ? (
          <p className="px-6 py-10 text-center text-muted">
            {data.queue.length === 0 ? "Nothing posted yet. Approved invoices show up here once they're in the ERP." : "Nothing here."}
          </p>
        ) : (
          <div className="overflow-x-auto px-2 py-2">
            <table className="w-full min-w-[920px] text-sm">
              <thead>
                <tr className="text-left text-xs text-muted">
                  <th className="px-4 py-3 font-medium">Invoice</th>
                  <th className="px-4 py-3 font-medium">Vendor</th>
                  <th className="px-4 py-3 text-right font-medium">Amount</th>
                  <th className="px-4 py-3 font-medium">Due</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">Details</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((item) => (
                  <QueueRow key={item.doc_number} item={item} today={data.today} onDone={refresh} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {released.length > 0 && (
        <Card className="flex flex-col gap-3">
          <CardTitle>Released runs</CardTitle>
          <ul className="flex flex-col divide-y divide-line text-sm">
            {released.map((b) => (
              <li key={b.batch_id} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2">
                <span className="font-mono text-[13px] text-navy">{b.batch_id}</span>
                <span>{b.count} payout(s)</span>
                <span className="font-mono">{formatINR(b.total)}</span>
                <span className="text-muted">
                  released by {b.released_by}
                  {b.released_at ? ` · ${new Date(b.released_at + "Z").toLocaleString("en-IN")}` : ""}
                </span>
                <span className="ml-auto flex gap-1">
                  {(["PAID", "PROCESSING", "QUEUED", "FAILED", "REVERSED"] as const).map((s) => {
                    const n = b.payments.filter((p) => p.status === s).length;
                    return n ? (
                      <Pill key={s} tone={s === "PAID" ? "approved" : s === "FAILED" || s === "REVERSED" ? "rejected" : "brand"}>
                        {n} {s.toLowerCase()}
                      </Pill>
                    ) : null;
                  })}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

function Tile({ icon: Icon, label, value, note }: { icon: typeof Send; label: string; value: string; note: string }) {
  return (
    <Card className="flex flex-col gap-1">
      <span className="flex items-center gap-2 text-[13px] text-muted">
        <Icon aria-hidden className="size-4" /> {label}
      </span>
      <span className="text-2xl font-semibold text-navy">{value}</span>
      <span className="text-xs text-muted">{note}</span>
    </Card>
  );
}

function ProposedBatch({ batch, queue, onDone }: { batch: PaymentBatch; queue: PayableItem[]; onDone: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const release = useMutation({
    mutationFn: () =>
      appsCall<{ message: string | null }>("POST", `/payments/batches/${encodeURIComponent(batch.batch_id)}/release`),
    onSettled: onDone,
  });
  const remove = useMutation({
    mutationFn: (id: number) => appsCall("POST", `/payments/${id}/cancel`),
    onSettled: onDone,
  });
  const vendor = (doc: string) => queue.find((q) => q.doc_number === doc);
  return (
    <div className="flex flex-col gap-3 rounded-control border border-line p-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-mono text-[13px] font-medium text-navy">{batch.batch_id}</span>
        <Pill tone="brand">Waiting for release</Pill>
        <span className="ml-auto font-mono text-sm">
          {batch.count} payment(s) · {formatINR(batch.total)}
        </span>
      </div>
      <ul className="flex flex-col divide-y divide-line text-sm">
        {batch.payments.map((p) => {
          const q = vendor(p.doc_number);
          return (
            <li key={p.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2">
              <span className="min-w-40">{q?.vendor_name ?? p.vendor_id}</span>
              <span className="font-mono text-[13px]">{q?.invoice_number ?? p.doc_number}</span>
              <span className="text-muted">due {formatDate(p.due_date)}</span>
              <span className="ml-auto font-mono">{formatINR(p.amount)}</span>
              <Button
                size="sm"
                variant="icon"
                className="size-8"
                aria-label={`Take ${q?.invoice_number ?? p.doc_number} out of this run`}
                disabled={remove.isPending || release.isPending}
                onClick={() => remove.mutate(p.id)}
              >
                <X aria-hidden />
              </Button>
            </li>
          );
        })}
      </ul>
      {release.error && <p className="text-sm text-rejected">{release.error.message}</p>}
      {confirming ? (
        <div className="flex flex-wrap items-center gap-3 rounded-control bg-canvas p-3 text-sm">
          <ShieldCheck aria-hidden className="size-5 text-brand-700" />
          <span className="flex-1">
            Send {batch.count} bank transfer(s) totalling <strong>{formatINR(batch.total)}</strong> now? You are releasing
            this run in your name.
          </span>
          <Button size="sm" variant="ghost" onClick={() => setConfirming(false)} disabled={release.isPending}>
            Cancel
          </Button>
          <Button size="sm" onClick={() => release.mutate()} disabled={release.isPending}>
            {release.isPending ? <Loader2 aria-hidden className="animate-spin" /> : <Send aria-hidden />} Release payments
          </Button>
        </div>
      ) : (
        <Button className="self-start" size="sm" onClick={() => setConfirming(true)}>
          <Send aria-hidden /> Release {batch.count} payment(s)
        </Button>
      )}
    </div>
  );
}

function QueueRow({ item, today, onDone }: { item: PayableItem; today: string; onDone: () => void }) {
  const state = STATES[item.state as StateKey] ?? STATES.SCHEDULED;
  const overdue = item.state !== "PAID" && item.due_date < today;
  const releaseHold = useMutation({
    mutationFn: () => appsCall("POST", `/payments/holds/${encodeURIComponent(item.doc_number)}/release`),
    onSettled: onDone,
  });
  const retry = useMutation({
    mutationFn: () => appsCall("POST", `/payments/${item.payment?.id}/retry`),
    onSettled: onDone,
  });
  const confirm = useMutation({
    mutationFn: () =>
      appsCall<{ status: string; message: string | null }>("POST", `/payments/${item.payment?.id}/check`),
    onSettled: onDone,
  });
  return (
    <tr className="border-t border-line align-top">
      <td className="px-4 py-3">
        {item.run_id ? (
          <Link href={`/runs/${item.run_id}`} className="font-mono text-[13px] hover:underline">
            {item.invoice_number}
          </Link>
        ) : (
          <span className="font-mono text-[13px]">{item.invoice_number}</span>
        )}
        <div className="text-xs text-muted">
          {item.doc_number}
          {item.system === "erpnext" ? " · ERPNext" : ""}
        </div>
      </td>
      <td className="px-4 py-3">{item.vendor_name ?? item.vendor_id}</td>
      <td className="px-4 py-3 text-right font-mono text-[13px]">{formatINR(item.amount)}</td>
      <td className={cn("px-4 py-3 text-[13px]", overdue && "font-semibold text-rejected")}>
        {formatDate(item.due_date)}
        {overdue && <div className="text-xs font-normal">overdue</div>}
      </td>
      <td className="px-4 py-3">
        <Pill tone={state.tone}>{state.label}</Pill>
      </td>
      <td className="max-w-sm px-4 py-3 text-xs">
        {item.state === "PAID" && (
          <span className="text-muted">
            UTR <span className="font-mono text-ink">{item.utr ?? item.payment?.utr ?? "—"}</span>
            {item.payment?.mode ? ` · ${item.payment.mode}` : ""}
          </span>
        )}
        {item.state === "IN_FLIGHT" && (
          <div className="flex flex-col gap-2">
            <span>Payment is being processed. Confirm once the vendor has received it.</span>
            <span className="text-muted">
              {item.payment?.payout_id ?? "sending"} · {item.payment?.provider_status ?? "queued"}
              {item.payment?.error ? ` · ${item.payment.error}` : ""}
            </span>
            {item.payment?.payout_id && (
              <Button
                size="sm"
                variant="soft"
                className="self-start"
                disabled={confirm.isPending}
                onClick={() => confirm.mutate()}
              >
                {confirm.isPending ? <Loader2 aria-hidden className="animate-spin" /> : <CheckCircle2 aria-hidden />}{" "}
                Confirm received
              </Button>
            )}
            {confirm.data?.message && <span className="text-muted">{confirm.data.message}</span>}
            {confirm.error && <span className="text-rejected">{confirm.error.message}</span>}
            {!confirm.data && (
              <span className="text-muted">Otherwise checked automatically 30 minutes after the last update.</span>
            )}
          </div>
        )}
        {item.state === "FAILED" && (
          <div className="flex flex-col gap-2">
            <span className="text-rejected">{item.payment?.error ?? "payout failed"}</span>
            <Button size="sm" variant="soft" className="self-start" disabled={retry.isPending} onClick={() => retry.mutate()}>
              <RotateCcw aria-hidden /> Retry in the next run
            </Button>
          </div>
        )}
        {item.holds.length > 0 && (
          <div className="flex flex-col gap-2">
            <ul className="list-disc pl-4 text-hold">
              {item.holds.map((h) => (
                <li key={h}>{h}</li>
              ))}
            </ul>
            {item.releasable && (
              <Button
                size="sm"
                variant="soft"
                className="self-start"
                disabled={releaseHold.isPending}
                onClick={() => releaseHold.mutate()}
              >
                <ShieldCheck aria-hidden /> Bank details verified: release hold
              </Button>
            )}
          </div>
        )}
        {item.state === "PROPOSED" && <span className="text-muted">in batch {item.payment?.batch_id}</span>}
        {item.state === "DUE" && <span className="text-muted">will be proposed in the next run</span>}
      </td>
    </tr>
  );
}
