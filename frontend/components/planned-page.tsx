import { Hammer } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, PageTitle } from "@/components/ui/card";

/** Placeholder for a page scheduled in a later build phase. */
export function PlannedPage({ title, phase, children }: { title: string; phase: number; children: string }) {
  return (
    <>
      <PageTitle>{title}</PageTitle>
      <Card className="flex max-w-2xl flex-col items-start gap-3">
        <span className="flex size-10 items-center justify-center rounded-control bg-brand-50 text-brand-700 dark:text-brand-500">
          <Hammer aria-hidden className="size-5" />
        </span>
        <h2 className="text-lg font-semibold text-navy">Arrives in phase {phase}</h2>
        <p className="text-muted">{children}</p>
        <Button asChild variant="soft" size="sm">
          <Link href="/reference">Browse vendors &amp; POs</Link>
        </Button>
      </Card>
    </>
  );
}
