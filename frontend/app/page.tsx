import { Building2, Upload } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, PageTitle } from "@/components/ui/card";

export default function NewRunPage() {
  return (
    <>
      <PageTitle>New run</PageTitle>
      <Card className="flex flex-col items-center gap-4 border-2 border-dashed border-brand-300/70 bg-brand-50/40 py-14 text-center shadow-none">
        <span className="flex size-12 items-center justify-center rounded-control bg-brand-50 text-brand-700 dark:text-brand-500">
          <Upload aria-hidden className="size-6" />
        </span>
        <div className="flex flex-col gap-1">
          <h2 className="text-lg font-semibold text-navy">Drop an invoice PDF here</h2>
          <p className="max-w-md text-muted">
            Upload and the one-click edge-case samples arrive with the LangGraph pipeline (phases 6–7). The reference
            data the agent matches against is already live.
          </p>
        </div>
        <div className="flex flex-wrap justify-center gap-3">
          <Button disabled>
            <Upload aria-hidden /> Upload invoice
          </Button>
          <Button asChild variant="soft">
            <Link href="/reference">
              <Building2 aria-hidden /> Vendors &amp; POs
            </Link>
          </Button>
        </div>
      </Card>
    </>
  );
}
