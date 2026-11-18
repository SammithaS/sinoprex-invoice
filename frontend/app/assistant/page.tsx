import { PlannedPage } from "@/components/planned-page";

export const metadata = { title: "Ask AP" };

export default function Page() {
  return (
    <PlannedPage title="Ask AP" phase={9}>
      Chat over policies and processed invoices, with cited answers.
    </PlannedPage>
  );
}
