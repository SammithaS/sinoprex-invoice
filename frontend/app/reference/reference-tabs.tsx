"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Ban, ChevronDown, RefreshCw } from "lucide-react";
import { Fragment, useState } from "react";
import { Pill } from "@/components/ui/badges";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  apiGet,
  type GoodsReceipt,
  type PurchaseOrder,
  type Vendor,
} from "@/lib/api/client";
import { cn, formatDate, formatINR, formatQty } from "@/lib/utils";

const TABS = [
  { id: "vendors", label: "Vendors" },
  { id: "pos", label: "Purchase orders" },
  { id: "grns", label: "Goods receipts" },
] as const;
type TabId = (typeof TABS)[number]["id"];

export function ReferenceTabs() {
  const [tab, setTab] = useState<TabId>("vendors");
  const vendors = useQuery({ queryKey: ["vendors"], queryFn: () => apiGet<Vendor[]>("/reference/vendors") });
  const pos = useQuery({ queryKey: ["pos"], queryFn: () => apiGet<PurchaseOrder[]>("/reference/pos") });
  const grns = useQuery({ queryKey: ["grns"], queryFn: () => apiGet<GoodsReceipt[]>("/reference/grns") });

  const counts: Record<TabId, number | undefined> = {
    vendors: vendors.data?.length,
    pos: pos.data?.length,
    grns: grns.data?.length,
  };

  return (
    <div className="flex flex-col gap-5">
      <div
        role="tablist"
        aria-label="Reference data"
        className="flex w-full gap-1 self-start overflow-x-auto rounded-control bg-card p-1 shadow-card sm:w-auto"
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            onClick={() => setTab(t.id)}
            className={cn(
              "flex h-11 shrink-0 items-center gap-2 rounded-[9px] px-4 text-[13px] font-medium",
              tab === t.id
                ? "bg-gradient-to-br from-brand-650 to-brand-700 text-white"
                : "text-muted hover:text-ink",
            )}
          >
            {t.label}
            {counts[t.id] !== undefined && (
              <span
                className={cn(
                  "rounded-full px-1.5 text-xs font-semibold",
                  tab === t.id ? "bg-white/20" : "bg-canvas",
                )}
              >
                {counts[t.id]}
              </span>
            )}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "vendors" && (
          <QueryState query={vendors}>{(data) => <VendorsTable vendors={data} />}</QueryState>
        )}
        {tab === "pos" && <QueryState query={pos}>{(data) => <PoTable pos={data} />}</QueryState>}
        {tab === "grns" && <QueryState query={grns}>{(data) => <GrnTable grns={data} />}</QueryState>}
      </div>
    </div>
  );
}

type Q<T> = { data: T | undefined; error: Error | null; isPending: boolean; refetch: () => unknown };

function QueryState<T>({ query, children }: { query: Q<T>; children: (data: T) => React.ReactNode }) {
  if (query.isPending) {
    return (
      <Card aria-busy="true" aria-label="Loading">
        <div className="flex flex-col gap-3">
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="h-10 animate-pulse rounded-lg bg-canvas" />
          ))}
        </div>
      </Card>
    );
  }
  if (query.error || query.data === undefined) {
    return (
      <Card role="alert" className="flex flex-col items-start gap-3">
        <span className="flex items-center gap-2 font-semibold text-rejected">
          <AlertTriangle aria-hidden className="size-4" />
          Couldn&apos;t load data from the backend
        </span>
        <p className="text-muted">{query.error?.message ?? "Unknown error"}. Is FastAPI running?</p>
        <Button variant="soft" size="sm" onClick={() => query.refetch()}>
          <RefreshCw aria-hidden /> Try again
        </Button>
      </Card>
    );
  }
  return <>{children(query.data)}</>;
}

function TableCard({ children, minWidth }: { children: React.ReactNode; minWidth: number }) {
  return (
    <Card className="p-0 sm:p-0">
      <div className="overflow-x-auto px-2 py-2">
        <table className="w-full border-collapse text-sm" style={{ minWidth }}>
          {children}
        </table>
      </div>
    </Card>
  );
}

const th = "px-4 py-3 text-left text-xs font-medium text-muted";
const td = "px-4 py-3.5 align-top";
const mono = "font-mono text-[13px] whitespace-nowrap";

function VendorsTable({ vendors }: { vendors: Vendor[] }) {
  return (
    <TableCard minWidth={860}>
      <thead>
        <tr>
          <th className={th}>Vendor</th>
          <th className={th}>GSTIN</th>
          <th className={th}>State</th>
          <th className={th}>Category</th>
          <th className={th}>Terms</th>
          <th className={th}>Bank</th>
          <th className={th}>Status</th>
        </tr>
      </thead>
      <tbody>
        {vendors.map((v) => (
          <tr key={v.id} className="border-t border-line">
            <td className={td}>
              <div className="font-medium text-navy">{v.name}</div>
              <div className="text-xs text-muted">{v.legal_name}</div>
              {v.aliases.length > 0 && (
                <div className="text-xs text-muted">aka {v.aliases.join(", ")}</div>
              )}
            </td>
            <td className={cn(td, mono)}>{v.gstin}</td>
            <td className={td}>
              {v.state} <span className="font-mono text-xs text-muted">({v.state_code})</span>
            </td>
            <td className={td}>{v.category}</td>
            <td className={td}>{v.payment_terms_days} days</td>
            <td className={cn(td, mono, "text-muted")}>••••{v.bank_account_last4}</td>
            <td className={td}>
              {v.status === "ACTIVE" ? (
                <Pill tone="approved">Active</Pill>
              ) : (
                <Pill tone="rejected">
                  <Ban aria-hidden className="size-3" /> Blocked
                </Pill>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </TableCard>
  );
}

const PO_STATUS: Record<string, { label: string; tone: "brand" | "review" | "neutral" }> = {
  OPEN: { label: "Open", tone: "brand" },
  PARTIALLY_INVOICED: { label: "Part-invoiced", tone: "review" },
  CLOSED: { label: "Closed", tone: "neutral" },
};

function PoTable({ pos }: { pos: PurchaseOrder[] }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <TableCard minWidth={900}>
      <thead>
        <tr>
          <th className={th}>
            <span className="sr-only">Expand</span>
          </th>
          <th className={th}>PO</th>
          <th className={th}>Vendor</th>
          <th className={th}>Date</th>
          <th className={cn(th, "text-right")}>Total</th>
          <th className={cn(th, "text-right")}>Remaining</th>
          <th className={th}>GRN</th>
          <th className={th}>Status</th>
        </tr>
      </thead>
      <tbody>
        {pos.map((po) => {
          const expanded = open === po.po_number;
          const status = PO_STATUS[po.status] ?? { label: po.status, tone: "neutral" as const };
          return (
            <Fragment key={po.po_number}>
              <tr className="border-t border-line">
                <td className="px-2 py-2">
                  <button
                    type="button"
                    aria-expanded={expanded}
                    aria-label={`${expanded ? "Hide" : "Show"} lines for ${po.po_number}`}
                    onClick={() => setOpen(expanded ? null : po.po_number)}
                    className="flex size-11 items-center justify-center rounded-control text-muted hover:bg-canvas"
                  >
                    <ChevronDown aria-hidden className={cn("size-4 transition-transform", expanded && "rotate-180")} />
                  </button>
                </td>
                <td className={cn(td, mono, "text-brand-700 dark:text-brand-500")}>{po.po_number}</td>
                <td className={cn(td, "font-medium text-navy")}>{po.vendor_name}</td>
                <td className={cn(td, "whitespace-nowrap text-muted")}>{formatDate(po.po_date)}</td>
                <td className={cn(td, "text-right font-semibold whitespace-nowrap text-navy")}>{formatINR(po.total)}</td>
                <td className={cn(td, "text-right whitespace-nowrap")}>{formatINR(po.remaining)}</td>
                <td className={td}>{po.requires_grn ? "Required" : <span className="text-muted">Not needed</span>}</td>
                <td className={td}>
                  <Pill tone={status.tone}>{status.label}</Pill>
                </td>
              </tr>
              {expanded && (
                <tr className="bg-canvas/60">
                  <td />
                  <td colSpan={7} className="px-4 pb-4">
                    <table className="w-full text-[13px]">
                      <thead>
                        <tr className="text-xs text-muted">
                          <th className="py-2 text-left font-medium">#</th>
                          <th className="py-2 text-left font-medium">Description</th>
                          <th className="py-2 text-left font-medium">HSN/SAC</th>
                          <th className="py-2 text-right font-medium">Qty</th>
                          <th className="py-2 text-right font-medium">Unit price</th>
                          <th className="py-2 text-right font-medium">GST</th>
                        </tr>
                      </thead>
                      <tbody>
                        {po.lines.map((ln) => (
                          <tr key={ln.line_no} className="border-t border-line">
                            <td className="py-2">{ln.line_no}</td>
                            <td className="py-2 text-navy">{ln.description}</td>
                            <td className="py-2 font-mono">{ln.hsn_sac}</td>
                            <td className="py-2 text-right">
                              {formatQty(ln.qty)} {ln.uom}
                            </td>
                            <td className="py-2 text-right">{formatINR(ln.unit_price)}</td>
                            <td className="py-2 text-right">{Number(ln.tax_rate)}%</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <p className="mt-2 text-xs text-muted">
                      Cost centre <span className="font-mono">{po.cost_centre}</span> · buyer {po.buyer} · already
                      invoiced {formatINR(po.amount_invoiced)}
                    </p>
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </TableCard>
  );
}

function GrnTable({ grns }: { grns: GoodsReceipt[] }) {
  return (
    <TableCard minWidth={760}>
      <thead>
        <tr>
          <th className={th}>GRN</th>
          <th className={th}>PO</th>
          <th className={th}>Vendor</th>
          <th className={th}>Received</th>
          <th className={th}>Lines received</th>
        </tr>
      </thead>
      <tbody>
        {grns.map((g) => (
          <tr key={g.grn_number} className="border-t border-line">
            <td className={cn(td, mono)}>{g.grn_number}</td>
            <td className={cn(td, mono, "text-muted")}>{g.po_number}</td>
            <td className={cn(td, "font-medium text-navy")}>{g.vendor_name}</td>
            <td className={td}>
              {formatDate(g.received_date)}
              <div className="text-xs text-muted">{g.received_by}</div>
            </td>
            <td className={td}>
              <ul className="flex flex-col gap-0.5">
                {g.lines.map((l) => (
                  <li key={l.po_line_no}>
                    {formatQty(l.qty_received)} × {l.description}
                  </li>
                ))}
              </ul>
            </td>
          </tr>
        ))}
      </tbody>
    </TableCard>
  );
}
