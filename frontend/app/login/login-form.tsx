"use client";

import { Loader2, LockKeyhole } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const res = await fetch("/api/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ password }),
    });
    setBusy(false);
    if (!res.ok) {
      setError("That password isn't right.");
      return;
    }
    const next = params.get("next") ?? "/";
    router.replace(next.startsWith("/") && !next.startsWith("//") ? next : "/");
    router.refresh();
  }

  return (
    <Card className="mx-auto mt-6 flex w-full max-w-sm flex-col gap-4">
      <span className="flex size-12 items-center justify-center rounded-control bg-brand-50 text-brand-700 dark:text-brand-500">
        <LockKeyhole aria-hidden className="size-6" />
      </span>
      <div className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold text-navy">Sign in to Sinoprex Invoice</h1>
        <p className="text-muted">This demo is password-protected. Ask the owner for the password.</p>
      </div>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label htmlFor="site-password" className="flex flex-col gap-1.5 text-[13px] font-medium text-navy">
          Password
          <input
            id="site-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="h-11 rounded-control border border-line bg-canvas px-3 text-sm text-ink focus:ring-2 focus:ring-brand-100 focus:outline-none"
          />
        </label>
        {error && (
          <p role="alert" className="text-[13px] text-rejected">
            {error}
          </p>
        )}
        <Button type="submit" disabled={busy || !password}>
          {busy && <Loader2 aria-hidden className="animate-spin" />} Sign in
        </Button>
      </form>
    </Card>
  );
}
