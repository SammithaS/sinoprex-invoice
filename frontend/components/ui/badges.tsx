import { Check, CircleAlert, Pause, X, type LucideIcon } from "lucide-react";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

export type Decision = "APPROVED" | "NEEDS_REVIEW" | "ON_HOLD" | "REJECTED";

const DECISIONS: Record<Decision, { label: string; icon: LucideIcon; className: string }> = {
  APPROVED: { label: "Approved", icon: Check, className: "bg-approved-bg text-approved" },
  NEEDS_REVIEW: { label: "Needs review", icon: CircleAlert, className: "bg-review-bg text-review" },
  ON_HOLD: { label: "On hold", icon: Pause, className: "bg-hold-bg text-hold" },
  REJECTED: { label: "Rejected", icon: X, className: "bg-rejected-bg text-rejected" },
};

/** Decision status: always icon + text, never colour alone. */
export function StatusBadge({ decision, className }: { decision: Decision; className?: string }) {
  const { label, icon: Icon, className: tone } = DECISIONS[decision];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold",
        tone,
        className,
      )}
    >
      <Icon aria-hidden className="size-3.5" strokeWidth={2.5} />
      {label}
    </span>
  );
}

type Tone = "approved" | "review" | "hold" | "rejected" | "neutral" | "brand";

const TONES: Record<Tone, string> = {
  approved: "bg-approved-bg text-approved",
  review: "bg-review-bg text-review",
  hold: "bg-hold-bg text-hold",
  rejected: "bg-rejected-bg text-rejected",
  neutral: "bg-canvas text-muted",
  brand: "bg-brand-50 text-brand-800",
};

export function Pill({ tone = "neutral", className, ...props }: ComponentProps<"span"> & { tone?: Tone }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold",
        TONES[tone],
        className,
      )}
      {...props}
    />
  );
}

/** ≥ 0.8 → approved colours; below → review colours. */
export function ConfidenceChip({ value }: { value: number }) {
  const ok = value >= 0.8;
  return (
    <Pill tone={ok ? "approved" : "review"} title={ok ? "High confidence" : "Low confidence — check"}>
      {Math.round(value * 100)}%
    </Pill>
  );
}

export function CitationChip({ children }: { children: string }) {
  return (
    <span className="inline-flex rounded-lg bg-canvas px-2.5 py-0.5 font-mono text-xs text-muted">
      {children}
    </span>
  );
}
