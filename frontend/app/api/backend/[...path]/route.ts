import type { NextRequest } from "next/server";

/**
 * Backend-for-frontend proxy: the browser calls /api/backend/<path>, and this
 * forwards to FastAPI with the shared API key, which never reaches the client.
 * Response bodies are streamed through, so Server-Sent Events work too.
 */
const API_URL = process.env.API_URL ?? "http://localhost:8000";
const API_KEY = process.env.API_KEY ?? "";

const FORWARD_REQUEST_HEADERS = ["content-type", "accept", "last-event-id", "x-actor"];
const FORWARD_RESPONSE_HEADERS = ["content-type", "cache-control", "content-disposition"];

async function proxy(req: NextRequest, ctx: RouteContext<"/api/backend/[...path]">) {
  const { path } = await ctx.params;
  const target = new URL(`${API_URL.replace(/\/$/, "")}/${path.map(encodeURIComponent).join("/")}`);
  target.search = req.nextUrl.search;

  const headers = new Headers();
  for (const name of FORWARD_REQUEST_HEADERS) {
    const value = req.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (API_KEY) headers.set("x-api-key", API_KEY);

  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body: hasBody ? req.body : undefined,
      // Required by Node's fetch when streaming a request body.
      ...(hasBody ? { duplex: "half" } : {}),
      cache: "no-store",
      signal: req.signal,
    } as RequestInit);
  } catch {
    return Response.json({ detail: "Backend is unreachable" }, { status: 502 });
  }

  const outHeaders = new Headers();
  for (const name of FORWARD_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) outHeaders.set(name, value);
  }
  return new Response(upstream.body, { status: upstream.status, headers: outHeaders });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const DELETE = proxy;
export const PATCH = proxy;
