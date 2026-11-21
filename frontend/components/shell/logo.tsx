import { FileCheck2 } from "lucide-react";
import { cn } from "@/lib/utils";

export function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <span className="flex items-center gap-2.5">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-[11px] bg-gradient-to-br from-[#34d399] to-brand-600 shadow-brand">
        <FileCheck2 aria-hidden className="size-[18px] text-white" strokeWidth={2.2} />
      </span>
      <span
        className={cn(
          "text-[18px] font-bold tracking-[-0.02em] whitespace-nowrap text-navy",
          compact && "sr-only",
        )}
      >
        SinoprexInvoice
      </span>
    </span>
  );
}
