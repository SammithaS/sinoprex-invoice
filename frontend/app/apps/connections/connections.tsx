"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Loader2, ShieldCheck, Trash2, Undo2, X } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useId, useState } from "react";
import { Pill } from "@/components/ui/badges";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { appsCall, type Connection, type ConnectionType } from "@/lib/api/apps";
import { ago } from "@/app/review/review-queue";
import { AppIcon } from "../app-icon";
import { FieldInput, inputClass, toFormValue } from "../field-input";

const STATUS_TONE = { OK: "approved", ERROR: "rejected", UNTESTED: "neutral" } as const;
const STATUS_LABEL = { OK: "Working", ERROR: "Failing", UNTESTED: "Not tested" } as const;

export function Connections() {
  const params = useSearchParams();
  const [adding, setAdding] = useState<string | null>(params.get("new"));
  const types = useQuery({
    queryKey: ["connection-types"],
    queryFn: () => appsCall<ConnectionType[]>("GET", "/connection-types"),
  });
  const conns = useQuery({
    queryKey: ["connections"],
    queryFn: () => appsCall<Connection[]>("GET", "/connections"),
  });

  if (types.isPending || conns.isPending) {
    return <div className="h-48 animate-pulse rounded-card bg-card shadow-card" aria-busy="true" />;
  }
  const error = types.error ?? conns.error;
  if (error || !types.data || !conns.data) {
    return (
      <p role="alert" className="text-rejected">
        Couldn&apos;t load connections: {error?.message}
      </p>
    );
  }
  const byId = Object.fromEntries(types.data.map((t) => [t.id, t]));

  return (
    <div className="flex flex-col gap-5">
      {adding && byId[adding] ? (
        <NewConnection type={byId[adding]} onDone={() => setAdding(null)} />
      ) : (
        <Card className="flex flex-col gap-3">
          <CardTitle>Add a connection</CardTitle>
          <ul className="grid gap-3 md:grid-cols-3">
            {types.data.map((t) => (
              <li key={t.id}>
                <button
                  onClick={() => setAdding(t.id)}
                  className="flex h-full w-full items-start gap-3 rounded-control border border-line p-3.5 text-left hover:bg-canvas"
                >
                  <AppIcon name={t.icon} className="size-10" />
                  <span className="flex flex-col gap-0.5">
                    <span className="font-semibold text-navy">{t.name}</span>
                    <span className="text-[13px] text-muted">{t.description}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {conns.data.length === 0 ? (
        <p className="text-muted">No connections yet.</p>
      ) : (
        <ul className="grid gap-4 lg:grid-cols-2">
          {conns.data.map((c) => (
            <li key={c.id}>
              <ConnectionCard conn={c} type={byId[c.type]} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SecretInput({
  label,
  help,
  value,
  onChange,
}: {
  label: string;
  help?: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const id = useId();
  return (
    <label htmlFor={id} className="flex flex-col gap-1.5 text-[13px] font-medium text-navy">
      <span className="inline-flex items-center gap-1.5">
        <KeyRound aria-hidden className="size-3.5 text-muted" /> {label}
      </span>
      <input
        id={id}
        type="password"
        autoComplete="new-password"
        spellCheck={false}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={inputClass}
      />
      {help && <span className="text-xs font-normal text-muted">{help}</span>}
    </label>
  );
}

function NewConnection({ type, onDone }: { type: ConnectionType; onDone: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [fields, setFields] = useState<Record<string, unknown>>(
    Object.fromEntries(type.fields.map((f) => [f.name, toFormValue(f, undefined)])),
  );
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const create = useMutation({
    mutationFn: async () => {
      const conn = await appsCall<Connection>("POST", "/connections", { type: type.id, name, fields, secrets });
      // Test right away so the card shows whether it works.
      return appsCall<Connection>("POST", `/connections/${conn.id}/test`);
    },
    onSuccess: () => {
      setSecrets({});
      void qc.invalidateQueries({ queryKey: ["connections"] });
      onDone();
    },
  });

  return (
    <Card className="flex flex-col gap-4">
      <CardHeader className="mb-0">
        <div className="flex items-center gap-3">
          <AppIcon name={type.icon} className="size-10" />
          <CardTitle>New {type.name}</CardTitle>
        </div>
        <Button variant="icon" size="icon" aria-label="Cancel" onClick={onDone}>
          <X aria-hidden />
        </Button>
      </CardHeader>
      {type.setup_help && <p className="rounded-control bg-canvas px-3.5 py-2.5 text-[13px] text-ink">{type.setup_help}</p>}
      <form
        className="flex flex-col gap-4"
        autoComplete="off"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <label className="flex flex-col gap-1.5 text-[13px] font-medium text-navy">
          Name
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={`e.g. AP ${type.name}`}
            className={inputClass}
          />
        </label>
        {type.fields.map((f) => (
          <FieldInput key={f.name} field={f} value={fields[f.name]} onChange={(v) => setFields({ ...fields, [f.name]: v })} />
        ))}
        {type.secrets.map((s) => (
          <SecretInput
            key={s.name}
            label={s.label}
            help={s.help}
            value={secrets[s.name] ?? ""}
            onChange={(v) => setSecrets({ ...secrets, [s.name]: v })}
          />
        ))}
        {create.error && (
          <p role="alert" className="text-[13px] text-rejected">
            {create.error.message}
          </p>
        )}
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? <Loader2 aria-hidden className="animate-spin" /> : <ShieldCheck aria-hidden />} Save
            and test
          </Button>
          <span className="text-xs text-muted">Encrypted before it is stored; never shown again.</span>
        </div>
      </form>
    </Card>
  );
}

function ConnectionCard({ conn, type }: { conn: Connection; type?: ConnectionType }) {
  const qc = useQueryClient();
  const [replacing, setReplacing] = useState<string | null>(null);
  const [value, setValue] = useState("");
  const done = (data?: Connection) => {
    if (data) qc.setQueryData<Connection[]>(["connections"], (old) => old?.map((c) => (c.id === data.id ? data : c)));
    void qc.invalidateQueries({ queryKey: ["connections"] });
  };
  const call = useMutation({
    mutationFn: async (action: { kind: "test" | "delete" | "replace" | "remove"; secret?: string }) => {
      if (action.kind === "test") return appsCall<Connection>("POST", `/connections/${conn.id}/test`);
      if (action.kind === "delete") {
        await appsCall<void>("DELETE", `/connections/${conn.id}`);
        return undefined;
      }
      const secretValue = action.kind === "replace" ? value : null;
      return appsCall<Connection>("PATCH", `/connections/${conn.id}`, {
        secrets: { [action.secret as string]: secretValue },
      });
    },
    onSuccess: (data) => {
      setReplacing(null);
      setValue("");
      done(data ?? undefined);
    },
  });
  const busy = call.isPending ? call.variables?.kind : null;

  return (
    <Card className="flex h-full flex-col gap-3">
      <div className="flex items-start gap-3">
        <AppIcon name={type?.icon ?? "workflow"} className="size-10" />
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="font-semibold break-words text-navy">{conn.name}</span>
          <span className="text-xs text-muted">{conn.type_name}</span>
        </div>
        <Pill tone={STATUS_TONE[conn.status]}>{STATUS_LABEL[conn.status]}</Pill>
      </div>

      {Object.keys(conn.fields).length > 0 && (
        <dl className="grid gap-1 text-[13px]">
          {type?.fields.map((f) =>
            conn.fields[f.name] ? (
              <div key={f.name} className="flex gap-2">
                <dt className="text-muted">{f.label}</dt>
                <dd className="break-all text-ink">{String(conn.fields[f.name])}</dd>
              </div>
            ) : null,
          )}
        </dl>
      )}

      <ul className="flex flex-col gap-2">
        {conn.secrets.map((s) => (
          <li key={s.name} className="flex flex-col gap-2 rounded-control bg-canvas px-3 py-2.5">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]">
              <KeyRound aria-hidden className="size-3.5 text-muted" />
              <span className="font-medium text-navy">{s.label}</span>
              {s.set ? (
                <span className="font-mono text-muted" title="Only the last 4 characters are ever shown">
                  ••••••••{s.hint}
                </span>
              ) : (
                <Pill tone="review">not set</Pill>
              )}
              {s.set && s.updated_at && (
                <span className="text-xs text-muted">
                  set {ago(s.updated_at)}
                  {s.updated_by ? ` by ${s.updated_by}` : ""}
                </span>
              )}
              <span className="ml-auto flex gap-1.5">
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-8"
                  onClick={() => {
                    setReplacing(replacing === s.name ? null : s.name);
                    setValue("");
                  }}
                >
                  {replacing === s.name ? <Undo2 aria-hidden /> : null}
                  {replacing === s.name ? "Cancel" : s.set ? "Replace" : "Set"}
                </Button>
                {s.set && (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-8"
                    disabled={call.isPending}
                    onClick={() => {
                      if (window.confirm(`Remove the ${s.label}? Apps using this connection stop working.`))
                        call.mutate({ kind: "remove", secret: s.name });
                    }}
                  >
                    Remove
                  </Button>
                )}
              </span>
            </div>
            {replacing === s.name && (
              <form
                autoComplete="off"
                className="flex flex-wrap items-end gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (value.trim()) call.mutate({ kind: "replace", secret: s.name });
                }}
              >
                <div className="min-w-0 flex-1">
                  <SecretInput label={`New ${s.label}`} value={value} onChange={setValue} />
                </div>
                <Button type="submit" size="sm" className="h-11" disabled={call.isPending || !value.trim()}>
                  {busy === "replace" && <Loader2 aria-hidden className="animate-spin" />} Save
                </Button>
              </form>
            )}
          </li>
        ))}
      </ul>

      {conn.status_message && (
        <p className={`text-[13px] break-words ${conn.status === "ERROR" ? "text-rejected" : "text-muted"}`}>
          {conn.status_message}
          {conn.tested_at && <span className="text-muted"> · tested {ago(conn.tested_at)}</span>}
        </p>
      )}
      {call.error && (
        <p role="alert" className="text-[13px] text-rejected">
          {call.error.message}
        </p>
      )}

      <div className="mt-auto flex flex-wrap items-center gap-2 pt-1">
        <Button size="sm" variant="soft" disabled={call.isPending} onClick={() => call.mutate({ kind: "test" })}>
          {busy === "test" ? <Loader2 aria-hidden className="animate-spin" /> : <ShieldCheck aria-hidden />} Test
        </Button>
        <Button
          size="sm"
          variant="danger"
          disabled={call.isPending || conn.used_by.length > 0}
          title={conn.used_by.length ? "In use by an app" : undefined}
          onClick={() => {
            if (window.confirm(`Delete ${conn.name}? Its secrets are destroyed.`)) call.mutate({ kind: "delete" });
          }}
        >
          <Trash2 aria-hidden /> Delete
        </Button>
        {conn.used_by.length > 0 && (
          <span className="text-xs text-muted">
            Used by{" "}
            {conn.used_by.map((id, i) => (
              <span key={id}>
                {i > 0 && ", "}
                <Link href={`/apps/${id}`} className="text-brand-800 underline">
                  {id}
                </Link>
              </span>
            ))}
          </span>
        )}
      </div>
    </Card>
  );
}

