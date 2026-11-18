import { PlannedPage } from "@/components/planned-page";

export const metadata = { title: "Procedure" };

export default function Page() {
  return (
    <PlannedPage title="Procedure" phase={8}>
      The versioned operating procedure: tolerances, approval matrix, owners and escalations.
    </PlannedPage>
  );
}
