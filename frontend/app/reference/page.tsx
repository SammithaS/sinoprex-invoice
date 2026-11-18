import type { Metadata } from "next";
import { PageTitle } from "@/components/ui/card";
import { ReferenceTabs } from "./reference-tabs";

export const metadata: Metadata = { title: "Vendors & POs" };

export default function ReferencePage() {
  return (
    <>
      <div className="flex flex-col gap-1">
        <PageTitle>Vendors &amp; POs</PageTitle>
        <p className="text-muted">
          Master data the agent matches invoices against: Acme Retail Pvt Ltd, Bengaluru (GST state 29).
        </p>
      </div>
      <ReferenceTabs />
    </>
  );
}
