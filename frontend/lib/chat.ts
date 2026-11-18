import type { ChatSource } from "@/lib/api/client";

/** Events of POST /chat (see backend app/assistant/service.py). */
export type ChatEvent =
  | { type: "thread"; thread_id: string }
  | { type: "tool_call"; name: string; args: Record<string, unknown> }
  | { type: "tool_result"; name: string; ok: boolean; sources: number }
  | { type: "token"; text: string }
  | {
      type: "answer";
      question: string;
      answer: string;
      citations: ChatSource[];
      dropped_citations: string[];
      tools: { name: string; args: Record<string, unknown> }[];
    }
  | { type: "error"; message: string };

/**
 * POST a question and read the Server-Sent Events from the response body
 * (EventSource only does GET, so the stream is parsed here).
 */
export async function streamChat(
  body: { message: string; thread_id?: string | null },
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/backend/chat", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try {
      const j = (await res.json()) as { detail?: unknown };
      if (typeof j.detail === "string") detail = j.detail;
    } catch {
      /* not JSON */
    }
    throw new Error(detail || `HTTP ${res.status}`);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value.replace(/\r\n/g, "\n");
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const data = block
        .split("\n")
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).trimStart())
        .join("\n");
      if (data) onEvent(JSON.parse(data) as ChatEvent);
    }
  }
}

/** "find_cases {vendor: TechNova}" → "Looked up cases · vendor TechNova". */
export function describeTool(name: string, args: Record<string, unknown>): string {
  const parts = Object.entries(args)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k.replace(/_/g, " ")} ${String(v)}`);
  const what: Record<string, string> = {
    find_cases: "Looked up cases",
    get_case: "Opened the case",
    search_policies: "Searched policies & contracts",
    case_stats: "Read the dashboard numbers",
  };
  const label = what[name] ?? name;
  return parts.length ? `${label} · ${parts.join(", ")}` : label;
}
