"use client";

import { useQuery } from "@tanstack/react-query";
import { Check, CircleAlert, Minus, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { Pill } from "@/components/ui/badges";
import { Card, CardTitle } from "@/components/ui/card";
import { apiGet, type Integrations as IntegrationsData } from "@/lib/api/client";
import { ago } from "@/app/review/review-queue";

const EVENT_LABEL: Record<string, string> = {
  review_needed: "Review needed",
  vendor_followup: "Vendor follow-up",
};

function Row({ ok, label, detail }: { ok: boolean | null; label: string; detail: string }) {
  const Icon: LucideIcon = ok === null ? Minus : ok ? Check : CircleAlert;
  return (
    <li className="flex items-start gap-2.5">
      <span
        className={
          ok === null
            ? "mt-0.5 text-muted"
            : ok
              ? "mt-0.5 text-approved"
              : "mt-0.5 text-review"
        }
      >
        <Icon aria-hidden className="size-4" />
      </span>
      <span className="flex flex-col">
        <span className="font-medium text-navy">{label}</span>
        <span className="text-[13px] break-words text-muted">{detail}</span>
      </span>
    </li>
  );
}

export function Integrations() {
  const q = useQuery({
    queryKey: ["integrations"],
    queryFn: () => apiGet<IntegrationsData>("/integrations"),
    refetchInterval: 15_000,
  });

  return (
    <Card className="flex max-w-2xl flex-col gap-4">
      <div className="flex flex-col gap-1">
        <CardTitle>Integrations · n8n</CardTitle>
        <p className="text-muted">
          Email intake, reviewer alerts, vendor follow-ups and the daily summary now run as{" "}
          <Link href="/apps" className="text-brand-800 underline">
            Apps
          </Link>{" "}
          inside SinoprexInvoice. n8n is optional: install the n8n Connector app to keep using external workflows
          (webhooks both ways are HMAC-signed).
        </p>
      </div>

      {q.isPending ? (
        <div className="h-24 animate-pulse rounded-control bg-canvas" aria-busy="true" />
      ) : q.error ? (
        <p role="alert" className="text-rejected">
          Couldn&apos;t load the integration status: {q.error.message}
        </p>
      ) : (
        <>
          <ul className="flex flex-col gap-3">
            <Row
              ok={q.data.intake_enabled}
              label="Email intake via n8n"
              detail={
                q.data.intake_enabled
                  ? "POST /intake/email accepts signed requests from n8n."
                  : "Off: use the Gmail Inbox Intake app, or install the n8n Connector app."
              }
            />
            <Row
              ok={q.data.n8n_configured}
              label="Notifications to n8n"
              detail={
                q.data.n8n_configured
                  ? Object.values(q.data.webhooks).join(" · ")
                  : "Off: the n8n Connector app isn't active. Notices are recorded as skipped."
              }
            />
            <Row
              ok={q.data.api_key_set}
              label="API key"
              detail={
                q.data.api_key_set
                  ? "Set: tunnelled requests must carry it."
                  : "Not set: fine on localhost, but requests through a tunnel are refused (except intake)."
              }
            />
          </ul>

          <div className="flex flex-col gap-2">
            <span className="text-xs font-semibold tracking-wide text-muted uppercase">Recent notifications</span>
            {q.data.recent.length === 0 ? (
              <p className="text-[13px] text-muted">None yet. A case that waits for a person sends one.</p>
            ) : (
              <ul className="flex flex-col divide-y divide-line">
                {q.data.recent.map((n) => (
                  <li key={n.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-sm">
                    <Pill tone={n.status === "SENT" ? "approved" : n.status === "FAILED" ? "rejected" : "neutral"}>
                      {n.status.toLowerCase()}
                    </Pill>
                    <span className="text-navy">{EVENT_LABEL[n.event] ?? n.event}</span>
                    <Link href={`/runs/${n.run_id}`} className="font-mono text-xs text-muted hover:text-brand-800">
                      {n.run_id.slice(0, 8)}
                    </Link>
                    <span className="ml-auto text-xs text-muted">
                      {ago(n.created_at)}
                      {n.attempts > 1 ? ` · ${n.attempts} attempts` : ""}
                    </span>
                    {n.error && <span className="w-full text-[13px] break-words text-muted">{n.error}</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </Card>
  );
}
