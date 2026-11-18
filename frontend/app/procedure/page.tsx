import type { Metadata } from "next";
import { PageTitle } from "@/components/ui/card";
import { ProcedureEditor } from "./procedure-editor";

export const metadata: Metadata = { title: "Procedure" };

export default function ProcedurePage() {
  return (
    <>
      <div className="flex flex-col gap-1">
        <PageTitle>Operating procedure</PageTitle>
        <p className="text-muted">
          The numbers the rules decide by — tolerances, approval matrix, owners. Every change is a new version and
          applies from the next case; each case records the version it ran under.
        </p>
      </div>
      <ProcedureEditor />
    </>
  );
}
