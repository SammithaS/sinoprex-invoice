"use client";

import { useQuery } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Pill } from "@/components/ui/badges";
import { buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { appsCall, STATE_LABEL, STATE_TONE, type AppSummary } from "@/lib/api/apps";
import { cn } from "@/lib/utils";
import { ago } from "@/app/review/review-queue";
import { AppIcon } from "./app-icon";

type Tab = "all" | "installed";

export function Marketplace() {
  const [tab, setTab] = useState<Tab>("all");
  const q = useQuery({
    queryKey: ["apps"],
    queryFn: () => appsCall<AppSummary[]>("GET", "/apps"),
    refetchInterval: 15_000,
  });
  const apps = q.data ?? [];
  const installed = apps.filter((a) => a.install);
  const shown = tab === "all" ? apps : installed;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div role="tablist" aria-label="Apps" className="inline-flex rounded-control bg-card p-1 shadow-card">
          {(
            [
              ["all", `Marketplace (${apps.length})`],
              ["installed", `Installed (${installed.length})`],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              role="tab"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
              className={cn(
                "h-9 rounded-[10px] px-4 text-[13px] font-semibold",
                tab === id ? "bg-brand-700 text-white" : "text-muted hover:text-navy",
              )}
            >
              {label}
            </button>
          ))}
        </div>
        <Link href="/apps/connections" className={buttonVariants({ variant: "ghost", size: "sm" })}>
          <KeyRound aria-hidden /> Connections
        </Link>
      </div>

      {q.isPending ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-48 animate-pulse rounded-card bg-card shadow-card" />
          ))}
        </div>
      ) : q.error ? (
        <p role="alert" className="text-rejected">
          Couldn&apos;t load the apps: {q.error.message}
        </p>
      ) : shown.length === 0 ? (
        <Card>
          <p className="text-muted">Nothing installed yet. Pick an app in the marketplace to get started.</p>
        </Card>
      ) : (
        <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {shown.map((a) => (
            <li key={a.id}>
              <AppCard app={a} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function AppCard({ app }: { app: AppSummary }) {
  const triggers = [
    ...app.events.map((e) => e.label),
    ...app.timers.map((t) => t.label),
  ];
  return (
    <Link
      href={`/apps/${app.id}`}
      className="flex h-full flex-col gap-3 rounded-card bg-card p-5 shadow-card transition-shadow hover:ring-2 hover:ring-brand-100"
    >
      <div className="flex items-start gap-3">
        <AppIcon name={app.icon} />
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="font-semibold text-navy">{app.name}</span>
          <span className="text-xs text-muted">
            {app.category} · {app.publisher}
          </span>
        </div>
        {app.install ? (
          <Pill tone={STATE_TONE[app.install.state]}>{STATE_LABEL[app.install.state]}</Pill>
        ) : (
          <Pill tone="brand">Install</Pill>
        )}
      </div>
      <p className="text-sm text-ink">{app.tagline}</p>
      <div className="mt-auto flex flex-wrap gap-1.5">
        {triggers.slice(0, 3).map((t) => (
          <span key={t} className="rounded-full bg-canvas px-2 py-0.5 text-[11px] text-muted">
            {t}
          </span>
        ))}
        {app.connections.map((c) => (
          <span key={c.name} className="rounded-full bg-canvas px-2 py-0.5 text-[11px] text-muted">
            Needs: {c.label}
          </span>
        ))}
      </div>
      {app.last_run && (
        <p className="text-xs text-muted">
          Last run {ago(app.last_run.started_at)} ·{" "}
          <span className={app.last_run.status === "ERROR" ? "text-rejected" : ""}>
            {app.last_run.status.toLowerCase()}
          </span>
        </p>
      )}
    </Link>
  );
}
