"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, History, Loader2, Plus, Save, Trash2 } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { Pill } from "@/components/ui/badges";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { apiGet, apiPost, type Policy, type Procedure } from "@/lib/api/client";
import { cn, formatINR } from "@/lib/utils";

type Band = { up_to: string | null; roles: string[] };

const input =
  "h-11 w-full rounded-control border border-line bg-canvas px-3 text-sm text-ink focus:bg-card";

function Field({ label, hint, children }: { label: string; hint?: string; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-[13px] font-medium text-navy">
        {label}
      </label>
      {children(id)}
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </div>
  );
}

function Section({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-col gap-0.5">
        <CardTitle>{title}</CardTitle>
        {note && <p className="text-[13px] text-muted">{note}</p>}
      </div>
      {children}
    </Card>
  );
}

function MapEditor({
  value,
  onChange,
  keyLabel,
  valueLabel,
}: {
  value: Record<string, string>;
  onChange: (v: Record<string, string>) => void;
  keyLabel: string;
  valueLabel: string;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[420px] text-sm">
        <thead>
          <tr className="text-left text-xs text-muted">
            <th className="pb-2 font-medium">{keyLabel}</th>
            <th className="pb-2 font-medium">{valueLabel}</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(value).map(([k, v]) => (
            <tr key={k} className="border-t border-line">
              <td className="py-2 pr-3 font-mono text-[13px]">{k}</td>
              <td className="py-2">
                <input
                  aria-label={`${valueLabel} for ${k}`}
                  className={cn(input, "h-10")}
                  value={v}
                  onChange={(e) => onChange({ ...value, [k]: e.target.value })}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ProcedureEditor() {
  const queryClient = useQueryClient();
  const history = useQuery({ queryKey: ["procedures"], queryFn: () => apiGet<Procedure[]>("/procedures") });
  const active = history.data?.find((p) => p.active);
  const [draft, setDraft] = useState<Policy | null>(null);
  const [baseVersion, setBaseVersion] = useState<number | null>(null);
  const [note, setNote] = useState("");

  // Start editing from the active version (and again whenever another version becomes active).
  if (active && baseVersion !== active.version) {
    setBaseVersion(active.version);
    setDraft(structuredClone(active.policy));
  }

  const save = useMutation({
    mutationFn: () => apiPost<Procedure>("/procedures", { policy: draft, note: note || null, created_by: "AP Manager", activate: true }),
    onSuccess: () => {
      setNote("");
      void queryClient.invalidateQueries({ queryKey: ["procedures"] });
    },
  });
  const activate = useMutation({
    mutationFn: (version: number) => apiPost<Procedure>(`/procedures/${version}/activate`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["procedures"] }),
  });

  if (history.isPending || !draft || !active) {
    return <div className="h-96 animate-pulse rounded-card bg-card shadow-card" aria-busy="true" />;
  }
  if (history.error) return <Card className="text-rejected">Couldn&apos;t load the procedure: {history.error.message}</Card>;

  const set = <K extends keyof Policy>(key: K, value: Policy[K]) => setDraft({ ...draft, [key]: value });
  const bands = (draft.approval_matrix ?? []) as Band[];
  const setBand = (i: number, band: Band) => set("approval_matrix", bands.map((b, j) => (j === i ? band : b)));
  const dirty = JSON.stringify(draft) !== JSON.stringify(active.policy);
  const next = Math.max(...history.data.map((p) => p.version)) + 1;

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
      <div className="flex flex-col gap-5">
        <Section title="Tolerances" note="Policy §3.2–3.7, §5.3. Lines above either price limit become a PRICE_VARIANCE exception.">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Price tolerance per line (%)">
              {(id) => (
                <input id={id} className={input} inputMode="decimal" value={draft.price_tolerance_pct}
                  onChange={(e) => set("price_tolerance_pct", e.target.value)} />
              )}
            </Field>
            <Field label="Price variance cap per invoice (₹)">
              {(id) => (
                <input id={id} className={input} inputMode="decimal" value={draft.price_tolerance_invoice}
                  onChange={(e) => set("price_tolerance_invoice", e.target.value)} />
              )}
            </Field>
            <Field label="Quantity tolerance (units)">
              {(id) => (
                <input id={id} className={input} inputMode="decimal" value={draft.qty_tolerance}
                  onChange={(e) => set("qty_tolerance", e.target.value)} />
              )}
            </Field>
            <Field label="Freight / handling cap (₹)">
              {(id) => (
                <input id={id} className={input} inputMode="decimal" value={draft.freight_cap}
                  onChange={(e) => set("freight_cap", e.target.value)} />
              )}
            </Field>
            <Field label="Rounding allowance (₹)">
              {(id) => (
                <input id={id} className={input} inputMode="decimal" value={draft.rounding}
                  onChange={(e) => set("rounding", e.target.value)} />
              )}
            </Field>
          </div>
        </Section>

        <Section title="Duplicates & confidence" note="Policy §4.2, §2.3, §9.">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Suspected-duplicate window (days)">
              {(id) => (
                <input id={id} className={input} type="number" min={0} value={draft.duplicate_window_days}
                  onChange={(e) => set("duplicate_window_days", Number(e.target.value))} />
              )}
            </Field>
            <Field label="Key-field confidence threshold" hint="0–1; below it, a person checks the field.">
              {(id) => (
                <input id={id} className={input} type="number" step={0.05} min={0} max={1} value={draft.confidence_threshold}
                  onChange={(e) => set("confidence_threshold", Number(e.target.value))} />
              )}
            </Field>
          </div>
        </Section>

        <Section title="Approval matrix" note="Policy §7.1. Bands by invoice total (incl. GST); roles approve in order.">
          <ul className="flex flex-col gap-3">
            {bands.map((b, i) => (
              <li key={i} className="grid items-end gap-3 sm:grid-cols-[180px_1fr_auto]">
                <Field label={i === bands.length - 1 && b.up_to === null ? "Above" : "Up to (₹)"}>
                  {(id) => (
                    <input id={id} className={input} inputMode="decimal" value={b.up_to ?? ""} placeholder="no limit"
                      onChange={(e) => setBand(i, { ...b, up_to: e.target.value === "" ? null : e.target.value })} />
                  )}
                </Field>
                <Field label="Approvers (comma-separated roles)">
                  {(id) => (
                    <input id={id} className={input} value={b.roles.join(", ")} placeholder="none — straight-through"
                      onChange={(e) => setBand(i, { ...b, roles: e.target.value.split(",").map((r) => r.trim()).filter(Boolean) })} />
                  )}
                </Field>
                <Button variant="icon" aria-label={`Remove band ${i + 1}`} onClick={() => set("approval_matrix", bands.filter((_, j) => j !== i))}>
                  <Trash2 aria-hidden />
                </Button>
              </li>
            ))}
          </ul>
          <Button variant="soft" size="sm" className="self-start"
            onClick={() => set("approval_matrix", [...bands.slice(0, -1), { up_to: "0", roles: [] }, ...bands.slice(-1)])}>
            <Plus aria-hidden /> Add band
          </Button>
        </Section>

        <Section title="Non-PO invoices & four-eyes" note="Policy §7.2, §6.2, §7.3.">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Non-PO: Finance Manager above (₹)">
              {(id) => (
                <input id={id} className={input} inputMode="decimal" value={draft.non_po_finance_above}
                  onChange={(e) => set("non_po_finance_above", e.target.value)} />
              )}
            </Field>
            <Field label="Non-PO allowed for vendor categories">
              {(id) => (
                <input id={id} className={input} value={(draft.non_po_categories ?? []).join(", ")}
                  onChange={(e) => set("non_po_categories", e.target.value.split(",").map((x) => x.trim()).filter(Boolean))} />
              )}
            </Field>
            <Field label="Bank change approvers (in order)">
              {(id) => (
                <input id={id} className={input} value={(draft.bank_change_approvers ?? []).join(", ")}
                  onChange={(e) => set("bank_change_approvers", e.target.value.split(",").map((x) => x.trim()).filter(Boolean))} />
              )}
            </Field>
            <Field label="Second approver for overrides" hint="Approving despite a price, quantity, balance, duplicate or tax exception.">
              {(id) => (
                <input id={id} className={input} value={draft.override_second_approver}
                  onChange={(e) => set("override_second_approver", e.target.value)} />
              )}
            </Field>
          </div>
        </Section>

        <Section title="Exception owners" note="Policy §8.1. “Buyer” means the buyer named on the PO.">
          <MapEditor keyLabel="Exception" valueLabel="Owner" value={draft.exception_owners ?? {}}
            onChange={(v) => set("exception_owners", v)} />
        </Section>

        <Section title="Approvers" note="Policy §7.4. Who holds each role.">
          <MapEditor keyLabel="Role" valueLabel="Name" value={draft.approvers ?? {}} onChange={(v) => set("approvers", v)} />
        </Section>
      </div>

      <div className="flex flex-col gap-5 xl:sticky xl:top-6">
        <Card className="flex flex-col gap-3">
          <CardTitle>Save as v{next}</CardTitle>
          <p className="text-[13px] text-muted">
            Editing {active.label}. A new version applies from the next case; cases already running keep the version
            they started with.
          </p>
          <Field label="What changed and why">
            {(id) => (
              <textarea id={id} rows={3} value={note} onChange={(e) => setNote(e.target.value)}
                className="resize-y rounded-control border border-line bg-canvas px-3 py-2.5 text-sm focus:bg-card"
                placeholder="e.g. Tighter price tolerance after Q3 audit" />
            )}
          </Field>
          <Button onClick={() => save.mutate()} disabled={!dirty || save.isPending}>
            {save.isPending ? <Loader2 aria-hidden className="animate-spin" /> : <Save aria-hidden />} Save &amp; activate v{next}
          </Button>
          {dirty && (
            <Button variant="ghost" size="sm" onClick={() => setDraft(structuredClone(active.policy))}>
              Discard changes
            </Button>
          )}
          {save.error && <p role="alert" className="text-[13px] text-rejected">{save.error.message}</p>}
        </Card>

        <Card className="flex flex-col gap-3">
          <CardHeader className="mb-0">
            <CardTitle className="flex items-center gap-2">
              <History aria-hidden className="size-4 text-muted" /> Version history
            </CardTitle>
          </CardHeader>
          <ol className="flex flex-col gap-3">
            {history.data.map((p) => (
              <li key={p.version} className="flex flex-col gap-1.5 rounded-control border border-line p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono font-semibold text-navy">{p.label}</span>
                  {p.active ? (
                    <Pill tone="approved">
                      <CheckCircle2 aria-hidden className="size-3.5" /> Active
                    </Pill>
                  ) : (
                    <Button variant="soft" size="sm" onClick={() => activate.mutate(p.version)} disabled={activate.isPending}>
                      Activate
                    </Button>
                  )}
                </div>
                <span className="text-xs text-muted">
                  {new Date(`${p.created_at}Z`).toLocaleString("en-IN")} · {p.created_by ?? "—"}
                </span>
                {p.note && <span className="text-[13px]">{p.note}</span>}
                {p.changes.length > 0 && (
                  <ul className="flex flex-col gap-0.5 font-mono text-xs">
                    {p.changes.map((c) => (
                      <li key={String(c.path)}>
                        {String(c.path)}: <span className="text-rejected line-through">{fmt(c.old)}</span> →{" "}
                        <span className="text-approved">{fmt(c.new)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ol>
        </Card>
      </div>
    </div>
  );
}

function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string" && /^\d+(\.\d+)?$/.test(v) && Number(v) >= 1000) return formatINR(v);
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}
