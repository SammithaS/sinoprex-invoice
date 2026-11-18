import { PlannedPage } from "@/components/planned-page";

export const metadata = { title: "Dashboard" };

export default function Page() {
  return (
    <PlannedPage title="Dashboard" phase={7}>
      Outcome KPIs, the gate funnel, exception causes and the runs table.
    </PlannedPage>
  );
}
