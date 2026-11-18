"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Loader2, NotebookTabs, Wrench } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Pill } from "@/components/ui/badges";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { apiGet, apiPost, type LedgerRow } from "@/lib/api/client";
import { formatDate, formatINR } from "@/lib/utils";

export function ErpLedger() {
  const ledger = useQuery({
    queryKey: ["erp"],
    queryFn: () => apiGet<LedgerRow[]>("/erp/documents"),
    refetchInterval: 15_000,
  });
  if (ledger.isPending) return <div className="h-60 animate-pulse rounded-card bg-card shadow-card" aria-busy="true" />;
  if (ledger.error) return <Card role="alert" className="text-rejected">Couldn&apos;t load the ledger: {ledger.error.message}</Card>;
  if (ledger.data.length === 0) {
    return (
      <Card className="flex flex-col items-center gap-3 py-12 text-center">
        <span className="flex size-12 items-center justify-center rounded-control bg-brand-50 text-brand-700 dark:text-brand-500">
          <NotebookTabs aria-hidden className="size-6" />
        </span>
        <h2 className="text-lg font-semibold text-navy">Nothing posted yet</h2>
        <p className="max-w-md text-muted">Approved invoices are posted here and then read back to check the write.</p>
      </Card>
    );
  }
  const verified = ledger.data.filter((r) => r.verified).length;
  return (
    <Card className="p-0 sm:p-0">
      <div className="flex flex-wrap items-center gap-2 px-[22px] pt-[22px] text-[13px] text-muted sm:px-6 sm:pt-6">
        {ledger.data.length} document(s) · {verified} verified by read-back ·{" "}
        {ledger.data.filter((r) => r.verified === false).length} mismatch
      </div>
      <div className="overflow-x-auto px-2 py-2">
        <table className="w-full min-w-[980px] text-sm">
          <thead>
            <tr className="text-left text-xs text-muted">
              <th className="px-4 py-3 font-medium">Document</th>
              <th className="px-4 py-3 font-medium">Invoice</th>
              <th className="px-4 py-3 font-medium">Vendor</th>
              <th className="px-4 py-3 text-right font-medium">Amount</th>
              <th className="px-4 py-3 font-medium">GL / cost centre</th>
              <th className="px-4 py-3 font-medium">PO</th>
              <th className="px-4 py-3 font-medium">Read-back</th>
            </tr>
          </thead>
          <tbody>
            {ledger.data.map((r) => (
              <Row key={r.document.doc_number} row={r} />
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Row({ row }: { row: LedgerRow }) {
  const d = row.document;
  const queryClient = useQueryClient();
  const [amount, setAmount] = useState("");
  const correct = useMutation({
    mutationFn: () => apiPost(`/erp/documents/${d.doc_number}/correct`, { amount }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["erp"] }),
  });
  return (
    <tr className="border-t border-line align-top">
      <td className="px-4 py-3 font-mono text-[13px] font-medium text-navy">{d.doc_number}</td>
      <td className="px-4 py-3">
        {row.run_id ? (
          <Link href={`/runs/${row.run_id}`} className="font-mono text-[13px] hover:underline">
            {d.invoice_number}
          </Link>
        ) : (
          <span className="font-mono text-[13px]">{d.invoice_number}</span>
        )}
        <div className="text-xs text-muted">{formatDate(d.invoice_date)}</div>
      </td>
      <td className="px-4 py-3 font-mono text-[13px]">{d.vendor_id}</td>
      <td className="px-4 py-3 text-right font-mono text-[13px]">
        {formatINR(d.amount)}
        <div className="text-xs text-muted">tax {formatINR(d.tax_amount)}</div>
      </td>
      <td className="px-4 py-3 font-mono text-[13px]">
        {d.gl_account ?? "—"} / {d.cost_centre ?? "—"}
      </td>
      <td className="px-4 py-3 font-mono text-[13px]">{d.po_number ?? "non-PO"}</td>
      <td className="px-4 py-3">
        {row.verified ? (
          <Pill tone="approved">
            <CheckCircle2 aria-hidden className="size-3.5" /> Verified
          </Pill>
        ) : row.verified === false ? (
          <div className="flex max-w-xs flex-col gap-2">
            <Pill tone="rejected" className="self-start">
              <AlertTriangle aria-hidden className="size-3.5" /> Mismatch
            </Pill>
            <ul className="text-xs text-rejected">
              {(row.mismatches ?? []).map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
            <div className="flex items-center gap-2">
              <input
                aria-label={`Corrected amount for ${d.doc_number}`}
                inputMode="decimal"
                placeholder="Correct amount"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                className="h-9 w-32 rounded-lg border border-line bg-canvas px-2 font-mono text-[13px]"
              />
              <Button size="sm" variant="soft" disabled={!amount || correct.isPending} onClick={() => correct.mutate()}>
                {correct.isPending ? <Loader2 aria-hidden className="animate-spin" /> : <Wrench aria-hidden />} Fix in ERP
              </Button>
            </div>
            {correct.isSuccess && (
              <span className="text-xs text-muted">
                Corrected. Re-check the posting on the{" "}
                <Link className="underline" href={`/runs/${row.run_id}`}>
                  case
                </Link>
                .
              </span>
            )}
          </div>
        ) : (
          <Pill>Not checked</Pill>
        )}
      </td>
    </tr>
  );
}
