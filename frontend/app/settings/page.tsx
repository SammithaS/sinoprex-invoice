import { PlannedPage } from "@/components/planned-page";

export const metadata = { title: "Settings" };

export default function Page() {
  return (
    <PlannedPage title="Settings" phase={12}>
      Demo reset and runtime settings.
    </PlannedPage>
  );
}
