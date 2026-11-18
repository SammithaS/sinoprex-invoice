import type { Metadata } from "next";
import { PageTitle } from "@/components/ui/card";
import { AskAp } from "./ask-ap";

export const metadata: Metadata = { title: "Ask AP" };

export default function AssistantPage() {
  return (
    <>
      <div className="flex flex-col gap-1">
        <PageTitle>Ask AP</PageTitle>
        <p className="text-muted">
          Questions about the AP policy, vendor contracts, GST rules and the invoices processed here. Every answer
          cites where it came from — and says so when it can&apos;t find something.
        </p>
      </div>
      <AskAp />
    </>
  );
}
