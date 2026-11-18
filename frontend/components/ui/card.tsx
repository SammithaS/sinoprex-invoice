import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

export function Card({ className, ...props }: ComponentProps<"section">) {
  return (
    <section
      className={cn("rounded-card bg-card p-[22px] shadow-card sm:p-6", className)}
      {...props}
    />
  );
}

export function CardHeader({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn("mb-4 flex flex-wrap items-center justify-between gap-3", className)}
      {...props}
    />
  );
}

export function CardTitle({ className, ...props }: ComponentProps<"h2">) {
  return (
    <h2 className={cn("text-lg leading-[26px] font-semibold text-navy", className)} {...props} />
  );
}

export function PageTitle({ className, ...props }: ComponentProps<"h1">) {
  return (
    <h1
      className={cn("text-[28px] leading-9 font-semibold tracking-[-0.02em] text-navy", className)}
      {...props}
    />
  );
}
