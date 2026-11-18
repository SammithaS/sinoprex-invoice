import { PlannedPage } from "@/components/planned-page";

export const metadata = { title: "ERP ledger" };

export default function Page() {
  return (
    <PlannedPage title="ERP ledger" phase={8}>
      Documents posted to the mock ERP and their read-back verification status.
    </PlannedPage>
  );
}
