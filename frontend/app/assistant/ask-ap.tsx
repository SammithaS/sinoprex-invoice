"use client";

import {
  ArrowUp,
  BookOpen,
  CircleAlert,
  ExternalLink,
  FileSearch,
  LayoutDashboard,
  Loader2,
  MessageSquare,
  RotateCcw,
  Search,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ApiError, apiGet, type ChatHistory, type ChatSource } from "@/lib/api/client";
import { describeTool, streamChat, type ChatEvent } from "@/lib/chat";
import { cn } from "@/lib/utils";

const THREAD_KEY = "askap.thread";

const SUGGESTIONS = [
  "Why was the TechNova invoice flagged?",
  "What price tolerance do we allow against a PO?",
  "Which invoices are waiting for review?",
  "Who has to approve an invoice above ₹5 lakh?",
];

const TOOL_ICONS: Record<string, LucideIcon> = {
  find_cases: Search,
  get_case: FileSearch,
  search_policies: BookOpen,
  case_stats: LayoutDashboard,
};

type Step = { name: string; label: string; done: boolean; ok: boolean };

type Turn = {
  question: string;
  answer: string; // final (citation-checked) answer, or the streaming draft
  citations: ChatSource[];
  steps: Step[];
  status: "streaming" | "done" | "error";
  error?: string;
};

function readThread(): string | null {
  try {
    return localStorage.getItem(THREAD_KEY);
  } catch {
    return null;
  }
}

function writeThread(id: string | null) {
  try {
    if (id) localStorage.setItem(THREAD_KEY, id);
    else localStorage.removeItem(THREAD_KEY);
  } catch {
    /* storage unavailable: the chat just won't survive a reload */
  }
}

export function AskAp() {
  const [threadId, setThreadId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [restoring, setRestoring] = useState(true);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  // Restore the last chat of this browser.
  useEffect(() => {
    const saved = readThread();
    if (!saved) {
      setRestoring(false);
      return;
    }
    apiGet<ChatHistory>(`/chat/${encodeURIComponent(saved)}`)
      .then((h) => {
        setThreadId(h.thread_id);
        setTurns(
          h.turns.map((t) => ({
            question: t.question,
            answer: t.answer,
            citations: t.citations,
            steps: (t.tools ?? []).map((u) => ({
              name: u.name,
              label: describeTool(u.name, u.args ?? {}),
              done: true,
              ok: true,
            })),
            status: "done" as const,
          })),
        );
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && (e.status === 404 || e.status === 422)) writeThread(null);
      })
      .finally(() => setRestoring(false));
    return () => abort.current?.abort();
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [turns]);

  const update = (fn: (t: Turn) => Turn) =>
    setTurns((prev) => (prev.length ? [...prev.slice(0, -1), fn(prev[prev.length - 1])] : prev));

  function onEvent(e: ChatEvent) {
    switch (e.type) {
      case "thread":
        setThreadId(e.thread_id);
        writeThread(e.thread_id);
        break;
      case "tool_call":
        // Text the model wrote before deciding to call a tool is not the answer.
        update((t) => ({
          ...t,
          answer: "",
          steps: [...t.steps, { name: e.name, label: describeTool(e.name, e.args), done: false, ok: true }],
        }));
        break;
      case "tool_result":
        update((t) => {
          const i = t.steps.findIndex((s) => s.name === e.name && !s.done);
          if (i < 0) return t;
          const steps = [...t.steps];
          steps[i] = { ...steps[i], done: true, ok: e.ok };
          return { ...t, steps };
        });
        break;
      case "token":
        update((t) => ({ ...t, answer: t.answer + e.text }));
        break;
      case "answer":
        update((t) => ({ ...t, answer: e.answer, citations: e.citations, status: "done" }));
        break;
      case "error":
        update((t) => ({ ...t, status: "error", error: e.message }));
        break;
    }
  }

  async function ask(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    setInput("");
    setBusy(true);
    setTurns((prev) => [...prev, { question: q, answer: "", citations: [], steps: [], status: "streaming" }]);
    const ctrl = new AbortController();
    abort.current = ctrl;
    try {
      await streamChat({ message: q, thread_id: threadId }, onEvent, ctrl.signal);
      update((t) =>
        t.status === "streaming" ? { ...t, status: "error", error: "The answer stopped before it finished." } : t,
      );
    } catch (err) {
      if (!ctrl.signal.aborted) {
        update((t) => ({ ...t, status: "error", error: err instanceof Error ? err.message : String(err) }));
      }
    } finally {
      setBusy(false);
      box.current?.focus();
    }
  }

  function newChat() {
    abort.current?.abort();
    setTurns([]);
    setThreadId(null);
    writeThread(null);
    setBusy(false);
    box.current?.focus();
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    void ask(input);
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void ask(input);
    }
  }

  return (
    <Card className="flex min-h-[calc(100dvh-15rem)] flex-col gap-0 p-0 sm:p-0">
      <div className="flex flex-1 flex-col gap-6 px-4 py-5 sm:px-6" aria-live="polite" aria-busy={busy}>
        {restoring ? (
          <div className="h-24 animate-pulse rounded-control bg-canvas" aria-label="Loading the chat" />
        ) : turns.length === 0 ? (
          <Empty onPick={(q) => void ask(q)} />
        ) : (
          turns.map((t, i) => <TurnView key={i} turn={t} index={i} />)
        )}
        <div ref={bottom} />
      </div>

      <form
        onSubmit={submit}
        className="sticky bottom-0 flex items-end gap-2 rounded-b-card border-t border-line bg-card px-4 py-3 sm:px-6"
      >
        {/* In the composer, not a header: it stays reachable however long the chat gets. */}
        <Button
          type="button"
          variant="icon"
          size="icon"
          onClick={newChat}
          disabled={turns.length === 0 && !busy}
          aria-label="New chat"
          title="New chat"
        >
          <RotateCcw aria-hidden />
        </Button>
        <label htmlFor="ask-input" className="sr-only">
          Ask a question
        </label>
        <textarea
          id="ask-input"
          ref={box}
          rows={1}
          value={input}
          maxLength={2000}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKey}
          placeholder="Ask a question…"
          className="max-h-40 min-h-11 flex-1 resize-none rounded-control bg-canvas px-3.5 py-2.5 text-ink placeholder:text-muted focus:ring-2 focus:ring-brand-100 focus:outline-none"
        />
        <Button type="submit" size="icon" disabled={busy || !input.trim()} aria-label="Send">
          {busy ? <Loader2 aria-hidden className="animate-spin" /> : <ArrowUp aria-hidden />}
        </Button>
      </form>
    </Card>
  );
}

function Empty({ onPick }: { onPick: (q: string) => void }) {
  return (
    <div className="m-auto flex max-w-xl flex-col items-center gap-4 py-8 text-center">
      <span className="flex size-12 items-center justify-center rounded-control bg-brand-50 text-brand-700 dark:text-brand-500">
        <MessageSquare aria-hidden className="size-6" />
      </span>
      <div className="flex flex-col gap-1">
        <h2 className="text-lg font-semibold text-navy">Ask about your AP work</h2>
        <p className="text-muted">
          Answers come from the policy, contracts and the cases processed here — never from guesses.
        </p>
      </div>
      <div className="grid w-full gap-2 sm:grid-cols-2">
        {SUGGESTIONS.map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => onPick(q)}
            className="rounded-control bg-canvas px-3.5 py-3 text-left text-sm text-ink transition-colors hover:bg-brand-50 focus-visible:ring-2 focus-visible:ring-brand-100 focus-visible:outline-none"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}

function TurnView({ turn, index }: { turn: Turn; index: number }) {
  const byCitation = new Map(turn.citations.map((c) => [c.citation, c]));
  const thinking = turn.status === "streaming" && !turn.answer;
  return (
    <div className="flex flex-col gap-3">
      <div className="ml-auto max-w-[85%] rounded-control bg-brand-50 px-3.5 py-2.5 text-ink">{turn.question}</div>

      <div className="flex max-w-full flex-col gap-3">
        {turn.steps.length > 0 && (
          <ul className="flex flex-col gap-1" aria-label="What the assistant looked up">
            {turn.steps.map((s, i) => {
              const Icon = TOOL_ICONS[s.name] ?? Search;
              return (
                <li key={i} className="flex items-center gap-2 text-[13px] text-muted">
                  {s.done ? (
                    <Icon aria-hidden className={cn("size-3.5", s.ok ? "text-brand-500" : "text-rejected")} />
                  ) : (
                    <Loader2 aria-hidden className="size-3.5 animate-spin" />
                  )}
                  <span className="break-words">{s.label}</span>
                </li>
              );
            })}
          </ul>
        )}

        {thinking && (
          <span className="flex items-center gap-2 text-sm text-muted">
            <Loader2 aria-hidden className="size-4 animate-spin" />
            {turn.steps.length ? "Reading what it found…" : "Thinking…"}
          </span>
        )}

        {turn.answer && (
          <div className="flex flex-col gap-2 leading-relaxed text-ink">
            <Prose text={turn.answer} sources={byCitation} index={index} />
            {turn.status === "streaming" && (
              <span aria-hidden className="inline-block h-4 w-1.5 animate-pulse rounded-sm bg-brand-500" />
            )}
          </div>
        )}

        {turn.status === "error" && (
          <div role="alert" className="flex items-start gap-2 rounded-control bg-rejected-bg px-3.5 py-2.5 text-rejected">
            <CircleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
            <span>{turn.error}</span>
          </div>
        )}

        {turn.status === "done" && turn.citations.length > 0 && <Sources sources={turn.citations} index={index} />}
      </div>
    </div>
  );
}

function sourceId(index: number, citation: string) {
  return `src-${index}-${citation.replace(/[^a-zA-Z0-9]+/g, "-")}`;
}

/** Paragraphs and "- " bullets, with **bold** and [citation] chips. */
function Prose({ text, sources, index }: { text: string; sources: Map<string, ChatSource>; index: number }) {
  const blocks: ReactNode[] = [];
  let bullets: string[] = [];
  const flush = () => {
    if (bullets.length) {
      blocks.push(
        <ul key={`ul-${blocks.length}`} className="ml-5 list-disc space-y-1">
          {bullets.map((b, i) => (
            <li key={i}>
              <Inline text={b} sources={sources} index={index} />
            </li>
          ))}
        </ul>,
      );
      bullets = [];
    }
  };
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    const bullet = /^([-*•]|\d+\.)\s+(.*)$/.exec(line);
    if (bullet) {
      bullets.push(bullet[2]);
      continue;
    }
    flush();
    if (line) {
      blocks.push(
        <p key={`p-${blocks.length}`}>
          <Inline text={line} sources={sources} index={index} />
        </p>,
      );
    }
  }
  flush();
  return <>{blocks}</>;
}

function Inline({ text, sources, index }: { text: string; sources: Map<string, ChatSource>; index: number }) {
  const parts = text.split(/(\[[^\]]+\]|\*\*[^*]+\*\*|`[^`]+`)/g);
  return (
    <>
      {parts.map((p, i) => {
        if (/^\*\*[^*]+\*\*$/.test(p)) return <strong key={i}>{p.slice(2, -2)}</strong>;
        if (/^`[^`]+`$/.test(p)) {
          return (
            <code key={i} className="rounded bg-canvas px-1 font-mono text-[0.85em] text-navy">
              {p.slice(1, -1)}
            </code>
          );
        }
        const m = /^\[([^\]]+)\]$/.exec(p);
        if (!m) return <span key={i}>{p}</span>;
        const src = sources.get(m[1]);
        return <SourceChip key={i} citation={m[1]} source={src} index={index} />;
      })}
    </>
  );
}

function SourceChip({ citation, source, index }: { citation: string; source?: ChatSource; index: number }) {
  const cls =
    "mx-0.5 inline-flex items-center gap-1 rounded-lg bg-canvas px-2 py-0.5 align-baseline font-mono text-xs text-muted transition-colors hover:bg-brand-50 hover:text-brand-800 focus-visible:ring-2 focus-visible:ring-brand-100 focus-visible:outline-none";
  if (source?.kind === "case" && source.run_id) {
    return (
      <Link href={`/runs/${source.run_id}`} className={cls} title={`Open case ${citation}`}>
        {citation}
      </Link>
    );
  }
  if (source?.kind === "stats") {
    return (
      <Link href="/dashboard" className={cls} title="Open the dashboard">
        {citation}
      </Link>
    );
  }
  if (source) {
    return (
      <a href={`#${sourceId(index, citation)}`} className={cls} title={source.title}>
        {citation}
      </a>
    );
  }
  // While streaming, citations aren't checked yet: plain chip.
  return <span className={cls}>{citation}</span>;
}

function Sources({ sources, index }: { sources: ChatSource[]; index: number }) {
  return (
    <div className="flex flex-col gap-2 border-t border-line pt-3">
      <span className="text-xs font-semibold tracking-wide text-muted uppercase">Sources</span>
      <ul className="flex flex-col gap-1.5">
        {sources.map((s) => (
          <li key={s.citation} id={sourceId(index, s.citation)} className="scroll-mt-24">
            {s.kind === "knowledge" ? (
              <details className="group rounded-control bg-canvas px-3 py-2">
                <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 text-sm">
                  <BookOpen aria-hidden className="size-3.5 text-brand-500" />
                  <span className="font-mono text-xs text-navy">{s.citation}</span>
                  {s.title !== s.citation && <span className="text-muted">{s.title}</span>}
                  <span className="ml-auto text-xs text-muted group-open:hidden">Show excerpt</span>
                </summary>
                {s.excerpt && (
                  <p className="mt-2 text-[13px] leading-relaxed whitespace-pre-line text-ink">{s.excerpt}</p>
                )}
              </details>
            ) : (
              <Link
                href={s.kind === "case" && s.run_id ? `/runs/${s.run_id}` : "/dashboard"}
                className="flex flex-wrap items-center gap-2 rounded-control bg-canvas px-3 py-2 text-sm hover:bg-brand-50"
              >
                {s.kind === "case" ? (
                  <FileSearch aria-hidden className="size-3.5 text-brand-500" />
                ) : (
                  <LayoutDashboard aria-hidden className="size-3.5 text-brand-500" />
                )}
                <span className="font-mono text-xs text-navy">{s.citation}</span>
                <span className="text-muted">{s.title}</span>
                <ExternalLink aria-hidden className="ml-auto size-3.5 text-muted" />
              </Link>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
