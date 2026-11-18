import type { Metadata } from "next";
import { PageTitle } from "@/components/ui/card";
import { Dashboard } from "./dashboard";

export const metadata: Metadata = { title: "Dashboard" };

export default function DashboardPage() {
  return (
    <>
      <div className="flex flex-col gap-1">
        <PageTitle>Dashboard</PageTitle>
        <p className="text-muted">How invoices move through the pipeline: outcomes, the review queue and where cases stop.</p>
      </div>
      <Dashboard />
    </>
  );
}
