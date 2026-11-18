import type { Metadata } from "next";
import { PageTitle } from "@/components/ui/card";
import { ErpLedger } from "./erp-ledger";

export const metadata: Metadata = { title: "ERP ledger" };

export default function ErpPage() {
  return (
    <>
      <div className="flex flex-col gap-1">
        <PageTitle>ERP ledger</PageTitle>
        <p className="text-muted">
          The mock ERP. Every approved invoice is posted once (the case id is the idempotency key), then read back
          and compared with what was approved; a difference keeps the case open until it&apos;s fixed.
        </p>
      </div>
      <ErpLedger />
    </>
  );
}
