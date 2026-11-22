"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, RotateCcw } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { apiPost } from "@/lib/api/client";

type ResetOut = { runs_deleted: number; erp_documents_deleted: number };

export function ResetDemo() {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const reset = useMutation({
    mutationFn: () => apiPost<ResetOut>("/admin/reset-demo"),
    onSuccess: () => {
      setConfirming(false);
      void queryClient.invalidateQueries();
    },
  });
  return (
    <Card className="flex max-w-2xl flex-col gap-3">
      <CardTitle>Start over</CardTitle>
      <p className="text-muted">
        Deletes every case (with its timeline, reviews, exceptions, approvals and corrections), empties the mock ERP
        ledger and reloads the seed invoice history. Operating-procedure versions are kept. The samples depend on run
        order — split invoices and duplicates see the cases before them — so reset before running them again.
      </p>
      {!confirming ? (
        <Button variant="danger" className="self-start" onClick={() => setConfirming(true)}>
          <RotateCcw aria-hidden /> Reset data
        </Button>
      ) : (
        <div className="flex flex-wrap items-center gap-2.5" role="group" aria-label="Confirm reset">
          <span className="text-[13px] font-medium text-rejected">This can&apos;t be undone.</span>
          <Button variant="danger" onClick={() => reset.mutate()} disabled={reset.isPending}>
            {reset.isPending ? <Loader2 aria-hidden className="animate-spin" /> : <RotateCcw aria-hidden />} Yes, reset
          </Button>
          <Button variant="ghost" onClick={() => setConfirming(false)}>
            Cancel
          </Button>
        </div>
      )}
      {reset.data && (
        <p role="status" className="text-[13px] text-approved">
          Done: {reset.data.runs_deleted} case(s) and {reset.data.erp_documents_deleted} ERP document(s) removed.
        </p>
      )}
      {reset.error && <p role="alert" className="text-[13px] text-rejected">{reset.error.message}</p>}
    </Card>
  );
}
