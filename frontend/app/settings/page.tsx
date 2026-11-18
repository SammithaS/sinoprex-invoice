import type { Metadata } from "next";
import { PageTitle } from "@/components/ui/card";
import { ResetDemo } from "./reset-demo";

export const metadata: Metadata = { title: "Settings" };

export default function SettingsPage() {
  return (
    <>
      <PageTitle>Settings</PageTitle>
      <ResetDemo />
    </>
  );
}
