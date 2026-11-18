import type { Metadata } from "next";
import { Suspense } from "react";
import { EvidenceView } from "./evidence-view";

export const metadata: Metadata = { title: "Evidence" };

export default function EvidencePage() {
  return (
    <Suspense fallback={<div className="h-96 animate-pulse rounded-card bg-card shadow-card" aria-busy="true" />}>
      <EvidenceView />
    </Suspense>
  );
}
