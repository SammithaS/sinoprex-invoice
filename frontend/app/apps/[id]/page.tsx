import type { Metadata } from "next";
import { Suspense } from "react";
import { AppView } from "./app-view";

export const metadata: Metadata = { title: "App" };

export default function AppPage() {
  return (
    <Suspense fallback={<div className="h-96 animate-pulse rounded-card bg-card shadow-card" aria-busy="true" />}>
      <AppView />
    </Suspense>
  );
}
