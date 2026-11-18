"use client";

import { useState } from "react";
import { Card } from "@/components/ui/card";
import { backendUrl } from "@/lib/api/client";
import { FIELD_LABELS, type Extraction } from "@/lib/run-state";
import { cn } from "@/lib/utils";

const PX_PER_POINT = 2; // GET /runs/{id}/pages/{n} renders 2 px per PDF point
const LABELS = Object.fromEntries(FIELD_LABELS);

/** Page images with a highlight over each field where it was read (plan §2b.2). */
export function PdfPreview({
  runId,
  fileName,
  pages,
  extraction,
}: {
  runId: string;
  fileName: string;
  pages: number;
  extraction?: Extraction;
}) {
  const [page, setPage] = useState(1);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const boxes = Object.entries(extraction?.evidence ?? {}).filter(
    ([, ev]) => ev.bbox && (ev.page ?? 1) === page && ev.value !== null,
  );
  const low = new Set(extraction?.low_confidence ?? []);

  return (
    <Card className="flex flex-col gap-3 p-[18px] sm:p-[18px]" aria-label="Invoice preview">
      <div className="flex items-center justify-between gap-2 text-[13px] text-muted">
        <a href={backendUrl(`/runs/${runId}/file`)} target="_blank" rel="noreferrer" className="truncate hover:text-ink">
          {fileName}
        </a>
        {pages > 1 ? (
          <div className="flex items-center gap-1">
            {Array.from({ length: pages }, (_, i) => (
              <button
                key={i}
                type="button"
                onClick={() => {
                  setPage(i + 1);
                  setSize(null);
                }}
                aria-label={`Page ${i + 1}`}
                aria-current={page === i + 1}
                className={cn(
                  "size-8 rounded-lg text-xs",
                  page === i + 1 ? "bg-brand-50 font-semibold text-brand-800" : "hover:bg-canvas",
                )}
              >
                {i + 1}
              </button>
            ))}
          </div>
        ) : (
          <span>Page 1 of 1</span>
        )}
      </div>
      <div className="relative overflow-hidden rounded-control border border-line bg-white">
        {/* eslint-disable-next-line @next/next/no-img-element -- rendered on demand by the backend */}
        <img
          src={backendUrl(`/runs/${runId}/pages/${page}`)}
          alt={`Page ${page} of ${fileName}`}
          className="block h-auto w-full"
          onLoad={(e) => setSize({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
        />
        {size &&
          boxes.map(([field, ev]) => {
            const [x0, y0, x1, y1] = ev.bbox!;
            const pct = (v: number, total: number) => `${((v * PX_PER_POINT) / total) * 100}%`;
            const isLow = low.has(field);
            return (
              <span
                key={field}
                title={`${LABELS[field] ?? field}: ${ev.value} (${Math.round(ev.confidence * 100)}%)`}
                className={cn(
                  "absolute rounded-[3px] border",
                  isLow ? "border-review bg-review-bg/50" : "border-brand-300 bg-brand-50/40",
                )}
                style={{
                  left: pct(x0 - 2, size.w),
                  top: pct(y0 - 2, size.h),
                  width: pct(x1 - x0 + 4, size.w),
                  height: pct(y1 - y0 + 4, size.h),
                }}
              />
            );
          })}
      </div>
      <span className="text-xs text-muted">
        Highlights show where each field was read; amber marks a low-confidence reading. Hover for the value.
      </span>
    </Card>
  );
}
