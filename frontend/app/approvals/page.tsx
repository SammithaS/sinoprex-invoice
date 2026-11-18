import { PlannedPage } from "@/components/planned-page";

export const metadata = { title: "Approvals" };

export default function Page() {
  return (
    <PlannedPage title="Approvals" phase={8}>
      Matrix-routed approver inbox, with four-eyes checks for sensitive overrides.
    </PlannedPage>
  );
}
