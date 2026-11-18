"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, MessageSquare, X } from "lucide-react";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { apiPost, type ReviewAction, type Run } from "@/lib/api/client";

/** Approve / request info / reject a paused run (POST /runs/{id}/review resumes the graph). */
export function ReviewForm({ runId, onDone }: { runId: string; onDone?: (action: ReviewAction) => void }) {
  const queryClient = useQueryClient();
  const commentId = useId();
  const [comment, setComment] = useState("");
  const review = useMutation({
    mutationFn: (action: ReviewAction) =>
      apiPost<Run>(`/runs/${runId}/review`, { action, comment: comment || null, reviewer: "AP Reviewer" }),
    onSuccess: (_run, action) => {
      setComment("");
      void queryClient.invalidateQueries({ queryKey: ["run", runId] });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({ queryKey: ["stats"] });
      onDone?.(action);
    },
  });
  const busy = review.isPending ? review.variables : null;

  return (
    <div className="flex flex-col gap-3">
      <label htmlFor={commentId} className="flex flex-col gap-1.5 text-[13px] font-medium text-navy">
        Comment
        <textarea
          id={commentId}
          rows={2}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="e.g. Checked against the scan; values are correct"
          className="resize-y rounded-control border border-line bg-card px-3 py-2.5 text-sm font-normal text-ink placeholder:text-muted"
        />
      </label>
      <div className="flex flex-wrap gap-2.5">
        <Button className="flex-1 basis-28" disabled={review.isPending} onClick={() => review.mutate("APPROVE")}>
          {busy === "APPROVE" ? <Loader2 aria-hidden className="animate-spin" /> : <Check aria-hidden />} Approve
        </Button>
        <Button
          variant="soft"
          className="flex-1 basis-28"
          disabled={review.isPending}
          onClick={() => review.mutate("REQUEST_INFO")}
        >
          {busy === "REQUEST_INFO" ? <Loader2 aria-hidden className="animate-spin" /> : <MessageSquare aria-hidden />}{" "}
          Request info
        </Button>
        <Button
          variant="danger"
          className="flex-1 basis-28"
          disabled={review.isPending}
          onClick={() => review.mutate("REJECT")}
        >
          {busy === "REJECT" ? <Loader2 aria-hidden className="animate-spin" /> : <X aria-hidden />} Reject
        </Button>
      </div>
      {review.error && (
        <p role="alert" className="text-[13px] text-rejected">
          {review.error.message}
        </p>
      )}
    </div>
  );
}
