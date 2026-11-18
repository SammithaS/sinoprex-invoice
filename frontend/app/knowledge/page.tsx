import type { Metadata } from "next";
import { PageTitle } from "@/components/ui/card";
import { KnowledgeBase } from "./knowledge-base";

export const metadata: Metadata = { title: "Knowledge base" };

export default function KnowledgePage() {
  return (
    <>
      <div className="flex flex-col gap-1">
        <PageTitle>Knowledge base</PageTitle>
        <p className="text-muted">
          The AP policy, GST rules, vendor FAQ and contracts the pipeline and Ask AP cite. Each numbered section is a
          citable chunk; try a search to see what retrieval finds.
        </p>
      </div>
      <KnowledgeBase />
    </>
  );
}
