"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  CircleAlert,
  Download,
  Loader2,
  Pause,
  Play,
  Power,
  Save,
  Trash2,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { Pill } from "@/components/ui/badges";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, PageTitle } from "@/components/ui/card";
import {
  appsCall,
  STATE_LABEL,
  STATE_TONE,
  type AppDetail,
  type AppRun,
  type Connection,
} from "@/lib/api/apps";
import { ago } from "@/app/review/review-queue";
import { AppIcon } from "../app-icon";
import { FieldInput, inputClass, toFormValue } from "../field-input";

const RUN_TONE = { OK: "approved", ERROR: "rejected", SKIPPED: "neutral" } as const;

export function AppView() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const router = useRouter();
  const app = useQuery({
    queryKey: ["app", id],
    queryFn: () => appsCall<AppDetail>("GET", `/apps/${id}`),
    refetchInterval: 10_000,
  });
  const conns = useQuery({
    queryKey: ["connections"],
    queryFn: () => appsCall<Connection[]>("GET", "/connections"),
  });

  // Unsaved edits; null → the form shows what is saved.
  const [draft, setDraft] = useState<{
    inputs: Record<string, unknown>;
    bindings: Record<string, number | null>;
  } | null>(null);
  const saved = app.data
    ? {
        inputs: Object.fromEntries(
          app.data.inputs.map((f) => [f.name, toFormValue(f, app.data.install?.inputs?.[f.name])]),
        ),
        bindings: { ...(app.data.install?.bindings ?? {}) } as Record<string, number | null>,
      }
    : { inputs: {}, bindings: {} };
  const { inputs, bindings } = draft ?? saved;
  const dirty = draft !== null;

  const refresh = (data?: AppDetail) => {
    if (data) qc.setQueryData(["app", id], data);
    void qc.invalidateQueries({ queryKey: ["apps"] });
    void qc.invalidateQueries({ queryKey: ["connections"] });
  };

  const act = useMutation({
    mutationFn: async (action: "install" | "save" | "activate" | "deactivate" | "run" | "uninstall") => {
      if (action === "install") return appsCall<AppDetail>("POST", `/apps/${id}/install`);
      if (action === "save" || action === "activate") {
        if (dirty || action === "save") {
          await appsCall<AppDetail>("PUT", `/apps/${id}/config`, { inputs, bindings });
          setDraft(null);
        }
        if (action === "save") return appsCall<AppDetail>("GET", `/apps/${id}`);
        return appsCall<AppDetail>("POST", `/apps/${id}/activate`);
      }
      if (action === "deactivate") return appsCall<AppDetail>("POST", `/apps/${id}/deactivate`);
      if (action === "run") {
        await appsCall<AppRun>("POST", `/apps/${id}/run`);
        return appsCall<AppDetail>("GET", `/apps/${id}`);
      }
      await appsCall<void>("DELETE", `/apps/${id}`);
      return undefined;
    },
    onSuccess: (data, action) => {
      if (action === "uninstall") {
        refresh();
        router.push("/apps");
        return;
      }
      refresh(data ?? undefined);
    },
  });

  if (app.isPending) {
    return <div className="h-96 animate-pulse rounded-card bg-card shadow-card" aria-busy="true" />;
  }
  if (app.error) {
    return (
      <p role="alert" className="text-rejected">
        Couldn&apos;t load this app: {app.error.message}
      </p>
    );
  }
  const a = app.data;
  const inst = a.install;
  const busy = act.isPending ? act.variables : null;
  const spin = (name: string, Icon: typeof Play) =>
    busy === name ? <Loader2 aria-hidden className="animate-spin" /> : <Icon aria-hidden />;

  return (
    <>
      <Link href="/apps" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-brand-800">
        <ArrowLeft aria-hidden className="size-4" /> All apps
      </Link>

      <div className="flex flex-wrap items-start gap-4">
        <AppIcon name={a.icon} className="size-14" />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2.5">
            <PageTitle>{a.name}</PageTitle>
            {inst && <Pill tone={STATE_TONE[inst.state]}>{STATE_LABEL[inst.state]}</Pill>}
          </div>
          <p className="text-muted">
            {a.category} · {a.publisher} · v{a.version}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {!inst ? (
            <Button disabled={act.isPending} onClick={() => act.mutate("install")}>
              {spin("install", Download)} Install
            </Button>
          ) : (
            <>
              {a.run_now && inst.state === "ACTIVE" && (
                <Button variant="soft" disabled={act.isPending} onClick={() => act.mutate("run")}>
                  {spin("run", Zap)} Run now
                </Button>
              )}
              {inst.state === "ACTIVE" ? (
                <Button variant="ghost" disabled={act.isPending} onClick={() => act.mutate("deactivate")}>
                  {spin("deactivate", Pause)} Pause
                </Button>
              ) : (
                <Button disabled={act.isPending} onClick={() => act.mutate("activate")}>
                  {spin("activate", Power)} {dirty ? "Save and activate" : "Activate"}
                </Button>
              )}
            </>
          )}
        </div>
      </div>

      {act.error && (
        <p role="alert" className="rounded-control bg-rejected-bg px-4 py-3 text-sm text-rejected">
          {act.error.message}
        </p>
      )}
      {inst?.state === "ERROR" && inst.error && (
        <div role="alert" className="flex items-start gap-2.5 rounded-control bg-rejected-bg px-4 py-3 text-sm text-rejected">
          <CircleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
          <span>
            <strong>The app can&apos;t run:</strong> {inst.error}
          </span>
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,380px)]">
        <div className="flex flex-col gap-5">
          <Card className="flex flex-col gap-3">
            <CardTitle>About</CardTitle>
            <p className="text-ink">{a.description}</p>
            <dl className="flex flex-col gap-3 text-sm">
              {a.events.length > 0 && (
                <div>
                  <dt className="text-xs font-semibold tracking-wide text-muted uppercase">Runs when</dt>
                  <dd>
                    <ul className="mt-1 flex flex-col gap-1 text-ink">
                      {a.events.map((e) => (
                        <li key={e.name} className="flex gap-2">
                          <span aria-hidden className="text-brand-500">•</span>
                          {e.label}
                        </li>
                      ))}
                    </ul>
                  </dd>
                </div>
              )}
              {a.timers.length > 0 && (
                <div>
                  <dt className="text-xs font-semibold tracking-wide text-muted uppercase">On a schedule</dt>
                  <dd>
                    <ul className="mt-1 flex flex-col gap-1 text-ink">
                      {a.timers.map((t) => (
                        <li key={t.name} className="flex gap-2">
                          <span aria-hidden className="text-brand-500">•</span>
                          {t.label}
                        </li>
                      ))}
                    </ul>
                  </dd>
                </div>
              )}
            </dl>
          </Card>

          {inst && (
            <Card className="flex flex-col gap-4">
              <CardHeader className="mb-0">
                <CardTitle>Settings</CardTitle>
                <Button size="sm" variant="soft" disabled={act.isPending || !dirty} onClick={() => act.mutate("save")}>
                  {spin("save", Save)} Save
                </Button>
              </CardHeader>

              {a.connections.map((req) => {
                const options = (conns.data ?? []).filter((c) => c.type === req.type);
                return (
                  <label key={req.name} className="flex flex-col gap-1.5 text-[13px] font-medium text-navy">
                    <span>
                      {req.label}
                      <span className="text-rejected"> *</span>
                    </span>
                    <select
                      value={bindings[req.name] ?? ""}
                      onChange={(e) => {
                        setDraft({
                          inputs,
                          bindings: { ...bindings, [req.name]: e.target.value ? Number(e.target.value) : null },
                        });
                      }}
                      className={inputClass}
                    >
                      <option value="">Choose a connection…</option>
                      {options.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name} {c.status === "OK" ? "· tested" : c.status === "ERROR" ? "· failing" : ""}
                        </option>
                      ))}
                    </select>
                    <span className="text-xs font-normal text-muted">
                      {options.length === 0 ? "No connection of this kind yet. " : ""}
                      <Link href={`/apps/connections?new=${req.type}`} className="text-brand-800 underline">
                        Add a connection
                      </Link>{" "}
                      (credentials are stored encrypted).
                    </span>
                  </label>
                );
              })}

              {a.inputs.map((f) => (
                <FieldInput
                  key={f.name}
                  field={f}
                  value={inputs[f.name]}
                  onChange={(v) => {
                    setDraft({ inputs: { ...inputs, [f.name]: v }, bindings });
                  }}
                />
              ))}
              {inst.state === "ACTIVE" && (
                <p className="text-xs text-muted">The app stays active; saving re-checks the settings.</p>
              )}
            </Card>
          )}
        </div>

        <div className="flex flex-col gap-5">
          {inst && (
            <Card className="flex flex-col gap-3">
              <CardTitle>Activity</CardTitle>
              {a.runs.length === 0 ? (
                <p className="text-[13px] text-muted">No runs yet.</p>
              ) : (
                <ul className="flex flex-col divide-y divide-line">
                  {a.runs.map((r) => (
                    <li key={r.id} className="flex flex-col gap-1 py-2.5 text-sm">
                      <div className="flex flex-wrap items-center gap-2">
                        <Pill tone={RUN_TONE[r.status]}>{r.status.toLowerCase()}</Pill>
                        <span className="font-mono text-xs text-muted">{r.trigger}</span>
                        <span className="ml-auto text-xs text-muted">{ago(r.started_at)}</span>
                      </div>
                      {(r.message || r.error) && (
                        <span className={`text-[13px] break-words ${r.error ? "text-rejected" : "text-ink"}`}>
                          {r.error ?? r.message}
                        </span>
                      )}
                      {r.run_id && (
                        <Link href={`/runs/${r.run_id}`} className="font-mono text-xs text-muted hover:text-brand-800">
                          case {r.run_id.slice(0, 8)}
                        </Link>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          )}

          {inst && a.audit.length > 0 && (
            <Card className="flex flex-col gap-3">
              <CardTitle>Audit</CardTitle>
              <ul className="flex flex-col gap-1.5 text-[13px]">
                {a.audit.map((e, i) => (
                  <li key={i} className="flex flex-wrap gap-x-2 text-muted">
                    <span className="font-medium text-ink">{e.action.replace("app.", "")}</span>
                    <span>{e.actor ?? "system"}</span>
                    <span className="ml-auto">{ago(e.at)}</span>
                    {e.detail && <span className="w-full break-words">{e.detail}</span>}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {inst && (
            <Card className="flex flex-col gap-2">
              <CardTitle>Uninstall</CardTitle>
              <p className="text-[13px] text-muted">
                Removes the settings and the activity log. Connections stay in Connections.
              </p>
              <Button
                variant="danger"
                size="sm"
                className="self-start"
                disabled={act.isPending}
                onClick={() => {
                  if (window.confirm(`Uninstall ${a.name}? Its settings and activity log are deleted.`))
                    act.mutate("uninstall");
                }}
              >
                {spin("uninstall", Trash2)} Uninstall
              </Button>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
