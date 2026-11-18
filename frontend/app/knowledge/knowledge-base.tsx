"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Check, CircleAlert, Loader2, RefreshCw, Search } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Pill } from "@/components/ui/badges";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { apiGet, apiPost, type KnowledgeDoc, type KnowledgeHit, type ReindexOut } from "@/lib/api/client";

const GROUPS: { type: string; label: string }[] = [
  { type: "policy", label: "AP policy" },
  { type: "gst", label: "GST rules" },
  { type: "faq", label: "Vendor FAQ" },
  { type: "contract", label: "Vendor contracts" },
];

export function KnowledgeBase() {
  const queryClient = useQueryClient();
  const docs = useQuery({ queryKey: ["knowledge"], queryFn: () => apiGet<KnowledgeDoc[]>("/knowledge") });
  const reindex = useMutation({
    mutationFn: () => apiPost<ReindexOut>("/admin/reindex"),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["knowledge"] }),
  });

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <Card>
        <CardHeader>
          <CardTitle>Documents</CardTitle>
          <Button variant="soft" size="sm" onClick={() => reindex.mutate()} disabled={reindex.isPending}>
            {reindex.isPending ? <Loader2 aria-hidden className="animate-spin" /> : <RefreshCw aria-hidden />}
            Re-index
          </Button>
        </CardHeader>
        {reindex.data && (
          <p role="status" className="mb-3 text-[13px] text-approved">
            Indexed {reindex.data.chunks} chunks: {reindex.data.added} added, {reindex.data.removed} removed,{" "}
            {reindex.data.unchanged} unchanged.
          </p>
        )}
        {reindex.error && (
          <p role="alert" className="mb-3 text-[13px] text-rejected">
            Re-index failed: {reindex.error.message}
          </p>
        )}
        {docs.isPending ? (
          <div className="h-64 animate-pulse rounded-control bg-canvas" aria-busy="true" />
        ) : docs.error ? (
          <p role="alert" className="text-rejected">
            Couldn&apos;t load the documents: {docs.error.message}
          </p>
        ) : docs.data.length === 0 ? (
          <p className="text-muted">No documents found in data/knowledge.</p>
        ) : (
          <div className="flex flex-col gap-4">
            {GROUPS.map((g) => {
              const rows = docs.data.filter((d) => d.doc_type === g.type);
              if (!rows.length) return null;
              return (
                <section key={g.type} className="flex flex-col gap-1.5">
                  <h3 className="text-xs font-semibold tracking-wide text-muted uppercase">{g.label}</h3>
                  <ul className="flex flex-col divide-y divide-line">
                    {rows.map((d) => {
                      const full = d.indexed_chunks === d.chunks;
                      return (
                        <li
                          key={d.source}
                          className="flex flex-col gap-1 py-2 text-sm sm:flex-row sm:items-center sm:gap-3"
                        >
                          <span className="flex min-w-0 flex-1 items-start gap-2">
                            <BookOpen aria-hidden className="mt-0.5 size-3.5 shrink-0 text-brand-500" />
                            <span className="min-w-0 break-words text-navy">{d.title}</span>
                          </span>
                          <span className="flex items-center gap-2 pl-[22px] sm:pl-0">
                            <span className="font-mono text-xs text-muted">{d.source}</span>
                            <Pill
                              tone={full ? "approved" : "review"}
                              title={full ? "Fully indexed" : "Re-index needed"}
                            >
                              {full ? (
                                <Check aria-hidden className="size-3" />
                              ) : (
                                <CircleAlert aria-hidden className="size-3" />
                              )}
                              {d.indexed_chunks}/{d.chunks}
                            </Pill>
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              );
            })}
          </div>
        )}
      </Card>
      <SearchCard />
    </div>
  );
}

function SearchCard() {
  const [query, setQuery] = useState("");
  const search = useMutation({
    mutationFn: (q: string) => apiPost<KnowledgeHit[]>("/knowledge/search", { query: q, k: 5 }),
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    if (query.trim()) search.mutate(query.trim());
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Try retrieval</CardTitle>
      </CardHeader>
      <form onSubmit={submit} className="flex gap-2">
        <label htmlFor="kb-search" className="sr-only">
          Search the knowledge base
        </label>
        <input
          id="kb-search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="e.g. price tolerance against the PO"
          className="h-11 min-w-0 flex-1 rounded-control bg-canvas px-3.5 text-ink placeholder:text-muted focus:ring-2 focus:ring-brand-100 focus:outline-none"
        />
        <Button type="submit" size="icon" aria-label="Search" disabled={search.isPending || !query.trim()}>
          {search.isPending ? <Loader2 aria-hidden className="animate-spin" /> : <Search aria-hidden />}
        </Button>
      </form>
      <div className="mt-4 flex flex-col gap-2" aria-live="polite">
        {search.error && (
          <p role="alert" className="text-[13px] text-rejected">
            Search failed: {search.error.message}
          </p>
        )}
        {search.data?.length === 0 && (
          <p className="text-muted">Nothing relevant — retrieval returns nothing rather than a weak guess.</p>
        )}
        {search.data?.map((h) => (
          <details key={h.citation} className="group rounded-control bg-canvas px-3.5 py-2.5">
            <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 text-sm">
              <span className="font-mono text-xs text-navy">{h.citation}</span>
              <span className="min-w-0 flex-1 break-words text-muted">{h.heading}</span>
              <span className="text-xs text-muted" title="Reciprocal-rank-fusion score">
                {h.score.toFixed(3)}
              </span>
            </summary>
            <p className="mt-2 text-[13px] leading-relaxed whitespace-pre-line text-ink">{h.text}</p>
          </details>
        ))}
        {!search.data && !search.error && (
          <p className="text-[13px] text-muted">
            Hybrid search: vector similarity (FastEmbed) + Postgres full-text, merged with reciprocal rank fusion.
          </p>
        )}
      </div>
    </Card>
  );
}
