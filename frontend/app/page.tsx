import { PageTitle } from "@/components/ui/card";
import { NewRun } from "./new-run";

export default function NewRunPage() {
  return (
    <>
      <div className="flex flex-col gap-1">
        <PageTitle>New run</PageTitle>
        <p className="text-muted">
          Upload a vendor invoice or try one of the edge cases. The AI extracts and retrieves; the rules decide; you
          approve the uncertain cases.
        </p>
      </div>
      <NewRun />
    </>
  );
}
