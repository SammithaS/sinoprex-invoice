import type { Metadata } from "next";
import { PageTitle } from "@/components/ui/card";
import { ApprovalsInbox } from "./approvals-inbox";

export const metadata: Metadata = { title: "Approvals" };

export default function ApprovalsPage() {
  return (
    <>
      <div className="flex flex-col gap-1">
        <PageTitle>Approvals</PageTitle>
        <p className="text-muted">
          Cases waiting for a named approver from the operating procedure. Each level is a different person
          (four-eyes); whoever reviewed or approved a case can&apos;t approve it again.
        </p>
      </div>
      <ApprovalsInbox />
    </>
  );
}
