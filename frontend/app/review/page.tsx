import type { Metadata } from "next";
import { PageTitle } from "@/components/ui/card";
import { ReviewQueue } from "./review-queue";

export const metadata: Metadata = { title: "Review queue" };

export default function ReviewPage() {
  return (
    <>
      <div className="flex flex-col gap-1">
        <PageTitle>Review queue</PageTitle>
        <p className="text-muted">
          Cases paused for a person: each one asks a specific question. Approve, reject or request information —
          the pipeline resumes where it stopped.
        </p>
      </div>
      <ReviewQueue />
    </>
  );
}
