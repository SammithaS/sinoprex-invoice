import type { Metadata } from "next";
import { PageTitle } from "@/components/ui/card";
import { Marketplace } from "./marketplace";

export const metadata: Metadata = { title: "Apps" };

export default function AppsPage() {
  return (
    <>
      <div className="flex flex-col gap-1">
        <PageTitle>Apps</PageTitle>
        <p className="max-w-3xl text-muted">
          Automations that run inside SinoprexInvoice: install one, fill in its settings, connect an account and
          activate it. Credentials live in Connections, encrypted, and are never shown again after you save them.
        </p>
      </div>
      <Marketplace />
    </>
  );
}
