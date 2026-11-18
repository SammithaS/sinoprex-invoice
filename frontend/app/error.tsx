"use client"; // Error boundaries must be Client Components

import { RotateCcw, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

/** Fallback for unexpected render errors in any page (the shell around it stays usable). */
export default function ErrorPage({ error, retry }: { error: unknown; retry: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  const message = error instanceof Error ? error.message : String(error);
  return (
    <Card role="alert" className="mx-auto mt-6 flex max-w-lg flex-col items-center gap-3 py-12 text-center">
      <span className="flex size-12 items-center justify-center rounded-control bg-rejected-bg text-rejected">
        <TriangleAlert aria-hidden className="size-6" />
      </span>
      <h1 className="text-xl font-semibold text-navy">Something went wrong</h1>
      <p className="max-w-sm break-words text-muted">{message || "This page failed to render."}</p>
      <div className="flex flex-wrap justify-center gap-2">
        <Button size="sm" onClick={() => retry()}>
          <RotateCcw aria-hidden /> Try again
        </Button>
        <Button asChild variant="ghost" size="sm">
          <Link href="/dashboard">Dashboard</Link>
        </Button>
      </div>
    </Card>
  );
}
