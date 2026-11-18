import type { Metadata } from "next";
import { Suspense } from "react";
import { RunView } from "./run-view";

export const metadata: Metadata = { title: "Run" };

function Skeleton() {
  return (
    <div className="grid gap-5 lg:grid-cols-3" aria-busy="true">
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-96 animate-pulse rounded-card bg-card shadow-card" />
      ))}
    </div>
  );
}

export default function RunPage() {
  // The run id comes from the URL at request time (cacheComponents: stream it in).
  return (
    <Suspense fallback={<Skeleton />}>
      <RunView />
    </Suspense>
  );
}
