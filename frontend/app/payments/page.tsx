import type { Metadata } from "next";
import { PageTitle } from "@/components/ui/card";
import { PaymentsView } from "./payments-view";

export const metadata: Metadata = { title: "Payments" };

export default function PaymentsPage() {
  return (
    <>
      <div className="flex flex-col gap-1">
        <PageTitle>Payments</PageTitle>
        <p className="text-muted">
          Posted invoices and when they fall due. A payment run proposes what&apos;s due before the next payment day;
          nothing is paid until a person releases the batch. Payouts go by bank transfer through RazorpayX.
        </p>
      </div>
      <PaymentsView />
    </>
  );
}
