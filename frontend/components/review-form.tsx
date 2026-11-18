"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, MessageSquare, RefreshCw, X } from "lucide-react";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { apiPost, type ReviewAction, type Run } from "@/lib/api/client";

const DEFAULT_REVIEWER = "Priya Nair";

/** Answer a paused run (POST /runs/{id}/review resumes the graph). Shows only the actions the
 *  pause allows: e.g. no Approve for an unknown supplier, only Re-check for an ERP mismatch. */
export function ReviewForm({
  runId,
  actions = ["APPROVE", "REJECT", "REQUEST_INFO"],
  onDone,
}: {
  runId: string;
  actions?: string[];
  onDone?: (action: ReviewAction) => void;
}) {
  const queryClient = useQueryClient();
  const commentId = useId();
  const nameId = useId();
  const [comment, setComment] = useState("");
  const [reviewer, setReviewer] = useState(DEFAULT_REVIEWER);
  const review = useMutation({
    mutationFn: (action: ReviewAction) =>
      apiPost<Run>(`/runs/${runId}/review`, { action, comment: comment || null, reviewer: reviewer || null }),
    onSuccess: (_run, action) => {
      setComment("");
      for (const key of [["run", runId], ["records", runId], ["runs"], ["stats"], ["approvals"]]) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
      onDone?.(action);
    },
  });
  const busy = review.isPending ? review.variables : null;
  const icon = (a: ReviewAction, Icon: typeof Check) =>
    busy === a ? <Loader2 aria-hidden className="animate-spin" /> : <Icon aria-hidden />;

  if (actions.length === 1 && actions[0] === "RECHECK") {
    return (
      <div className="flex flex-col gap-2">
        <Button className="self-start" disabled={review.isPending} onClick={() => review.mutate("RECHECK")}>
          {icon("RECHECK", RefreshCw)} Re-check the ERP posting
        </Button>
        {review.error && <p role="alert" className="text-[13px] text-rejected">{review.error.message}</p>}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="grid gap-3 sm:grid-cols-[minmax(0,200px)_1fr]">
        <label htmlFor={nameId} className="flex flex-col gap-1.5 text-[13px] font-medium text-navy">
          Reviewer
          <input id={nameId} value={reviewer} onChange={(e) => setReviewer(e.target.value)}
            className="h-11 rounded-control border border-line bg-card px-3 text-sm font-normal text-ink" />
        </label>
        <label htmlFor={commentId} className="flex flex-col gap-1.5 text-[13px] font-medium text-navy">
          Comment
          <textarea id={commentId} rows={1} value={comment} onChange={(e) => setComment(e.target.value)}
            placeholder="e.g. Checked against the scan; values are correct"
            className="min-h-11 resize-y rounded-control border border-line bg-card px-3 py-2.5 text-sm font-normal text-ink placeholder:text-muted" />
        </label>
      </div>
      <div className="flex flex-wrap gap-2.5">
        {actions.includes("APPROVE") && (
          <Button className="flex-1 basis-28" disabled={review.isPending} onClick={() => review.mutate("APPROVE")}>
            {icon("APPROVE", Check)} Approve
          </Button>
        )}
        {actions.includes("REQUEST_INFO") && (
          <Button variant="soft" className="flex-1 basis-28" disabled={review.isPending} onClick={() => review.mutate("REQUEST_INFO")}>
            {icon("REQUEST_INFO", MessageSquare)} Request info
          </Button>
        )}
        {actions.includes("REJECT") && (
          <Button variant="danger" className="flex-1 basis-28" disabled={review.isPending} onClick={() => review.mutate("REJECT")}>
            {icon("REJECT", X)} Reject
          </Button>
        )}
      </div>
      {review.error && (
        <p role="alert" className="text-[13px] text-rejected">
          {review.error.message}
        </p>
      )}
    </div>
  );
}
