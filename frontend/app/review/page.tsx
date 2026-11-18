import { PlannedPage } from "@/components/planned-page";

export const metadata = { title: "Review queue" };

export default function Page() {
  return (
    <PlannedPage title="Review queue" phase={7}>
      Paused runs waiting for a person, with approve, reject and request-info actions.
    </PlannedPage>
  );
}
