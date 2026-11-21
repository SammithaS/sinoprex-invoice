import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { ArrowLeft } from "lucide-react";
import { PageTitle } from "@/components/ui/card";
import { Connections } from "./connections";

export const metadata: Metadata = { title: "Connections" };

export default function ConnectionsPage() {
  return (
    <>
      <Link href="/apps" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-brand-800">
        <ArrowLeft aria-hidden className="size-4" /> All apps
      </Link>
      <div className="flex flex-col gap-1">
        <PageTitle>Connections</PageTitle>
        <p className="max-w-3xl text-muted">
          Accounts and keys that apps use. Secrets are encrypted on the server (Fernet, key outside the
          database) and are write-only: after saving you only see the last 4 characters and when it was set.
          Replace or remove a secret at any time.
        </p>
      </div>
      <Suspense fallback={<div className="h-48 animate-pulse rounded-card bg-card shadow-card" aria-busy="true" />}>
        <Connections />
      </Suspense>
    </>
  );
}
