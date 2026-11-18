"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, FileText, Loader2, Play, ScanLine, Upload } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState, type DragEvent } from "react";
import { Pill, StatusBadge } from "@/components/ui/badges";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { apiGet, apiPost, apiUpload, type Run, type Sample } from "@/lib/api/client";
import { asDecision } from "@/lib/run-state";
import { cn } from "@/lib/utils";

const EDGE_LABELS: Record<string, string> = {
  clean: "Clean",
  tolerance: "Tolerance",
  other: "Other check",
};

function edgeLabel(edge: string): string {
  if (edge.startsWith("#")) return `Edge case ${edge}`;
  return EDGE_LABELS[edge] ?? edge;
}

export function NewRun() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const samples = useQuery({ queryKey: ["samples"], queryFn: () => apiGet<Sample[]>("/samples") });

  const started = (run: Run) => {
    void queryClient.invalidateQueries({ queryKey: ["runs"] });
    router.push(`/runs/${run.id}`);
  };
  const upload = useMutation({ mutationFn: (file: File) => apiUpload<Run>("/runs", file), onSuccess: started });
  const sample = useMutation({
    mutationFn: (key: string) => apiPost<Run>(`/samples/${encodeURIComponent(key)}/run`),
    onSuccess: started,
  });

  const pick = (files: FileList | null) => {
    const file = files?.[0];
    if (file) upload.mutate(file);
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    pick(e.dataTransfer.files);
  };
  const error = upload.error ?? sample.error;

  return (
    <div className="flex flex-col gap-6">
      <Card
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          "flex flex-col items-center gap-4 border-2 border-dashed py-12 text-center shadow-none transition-colors",
          dragging ? "border-brand-500 bg-brand-50" : "border-brand-300/70 bg-brand-50/40",
        )}
      >
        <span className="flex size-12 items-center justify-center rounded-control bg-brand-50 text-brand-700 dark:text-brand-500">
          {upload.isPending ? <Loader2 aria-hidden className="size-6 animate-spin" /> : <Upload aria-hidden className="size-6" />}
        </span>
        <div className="flex flex-col gap-1">
          <h2 className="text-lg font-semibold text-navy">Drop an invoice PDF here</h2>
          <p className="max-w-md text-muted">
            Digital or scanned, up to 10 MB. The case opens live: every stage, the checks it ran and the decision
            with its sources.
          </p>
        </div>
        <input
          ref={input}
          type="file"
          accept="application/pdf,.pdf"
          className="sr-only"
          aria-label="Choose an invoice PDF"
          onChange={(e) => pick(e.target.files)}
        />
        <Button onClick={() => input.current?.click()} disabled={upload.isPending}>
          <Upload aria-hidden /> {upload.isPending ? "Uploading…" : "Upload invoice"}
        </Button>
      </Card>

      {error && (
        <Card role="alert" className="flex items-center gap-2 py-4 text-rejected">
          <AlertTriangle aria-hidden className="size-4 shrink-0" />
          <span>Couldn&apos;t start the run: {error.message}</span>
        </Card>
      )}

      <Card>
        <CardHeader>
          <div className="flex flex-col gap-0.5">
            <CardTitle>Try a sample</CardTitle>
            <p className="text-[13px] text-muted">
              25 generated invoices, one per edge case. Run them in this order for the split-invoice and duplicate
              cases to see their earlier invoices.
            </p>
          </div>
        </CardHeader>
        {samples.isPending && (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-busy="true">
            {Array.from({ length: 6 }, (_, i) => (
              <div key={i} className="h-32 animate-pulse rounded-control bg-canvas" />
            ))}
          </div>
        )}
        {samples.error && <p className="text-rejected">Couldn&apos;t load samples: {samples.error.message}</p>}
        {samples.data && (
          <ol className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {samples.data.map((s, i) => {
              const expected = asDecision(s.expected_decision);
              const busy = sample.isPending && sample.variables === s.key;
              return (
                <li key={s.key} className="flex flex-col gap-3 rounded-control border border-line p-4">
                  <div className="flex items-start gap-3">
                    <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-canvas text-muted">
                      {s.is_scanned ? <ScanLine aria-hidden className="size-4" /> : <FileText aria-hidden className="size-4" />}
                    </span>
                    <div className="flex min-w-0 flex-col gap-0.5">
                      <span className="text-[13px] text-muted">
                        {i + 1}. {edgeLabel(s.edge_case)}
                        {s.is_scanned ? " · scanned" : ""}
                      </span>
                      <span className="leading-5 font-medium text-navy">{s.title}</span>
                    </div>
                  </div>
                  <div className="mt-auto flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-xs text-muted">Expected</span>
                      {expected ? <StatusBadge decision={expected} /> : <Pill>{s.expected_decision}</Pill>}
                    </div>
                    <Button
                      size="sm"
                      variant="soft"
                      onClick={() => sample.mutate(s.key)}
                      disabled={sample.isPending}
                      aria-label={`Run sample: ${s.title}`}
                    >
                      {busy ? <Loader2 aria-hidden className="animate-spin" /> : <Play aria-hidden />} Run
                    </Button>
                  </div>
                  {s.depends_on.length > 0 && (
                    <p className="text-xs text-muted">After: {s.depends_on.join(", ")}</p>
                  )}
                </li>
              );
            })}
          </ol>
        )}
      </Card>
    </div>
  );
}
