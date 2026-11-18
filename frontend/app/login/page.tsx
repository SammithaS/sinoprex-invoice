import type { Metadata } from "next";
import { Suspense } from "react";
import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in" };

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="mx-auto mt-6 h-64 max-w-sm animate-pulse rounded-card bg-card shadow-card" />}>
      <LoginForm />
    </Suspense>
  );
}
