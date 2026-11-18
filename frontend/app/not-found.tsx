import { FileQuestion } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

export default function NotFound() {
  return (
    <Card className="mx-auto mt-6 flex max-w-lg flex-col items-center gap-3 py-12 text-center">
      <span className="flex size-12 items-center justify-center rounded-control bg-brand-50 text-brand-700 dark:text-brand-500">
        <FileQuestion aria-hidden className="size-6" />
      </span>
      <h1 className="text-xl font-semibold text-navy">Page not found</h1>
      <p className="max-w-sm text-muted">
        This address doesn&apos;t exist. Cases are under the review queue or the dashboard&apos;s recent runs.
      </p>
      <div className="flex flex-wrap justify-center gap-2">
        <Button asChild size="sm">
          <Link href="/dashboard">Dashboard</Link>
        </Button>
        <Button asChild variant="ghost" size="sm">
          <Link href="/">New run</Link>
        </Button>
      </div>
    </Card>
  );
}
