/** Apps marketplace + connections. The backend returns these shapes from /apps and /connections. */
import { ApiError } from "./client";

export const ACTOR = "Sammitha S Poojary";

export type FieldKind =
  | "text"
  | "email"
  | "emails"
  | "url"
  | "number"
  | "boolean"
  | "select"
  | "multiselect"
  | "time"
  | "textarea";

export type AppField = {
  name: string;
  label: string;
  kind: FieldKind;
  required: boolean;
  default: unknown;
  help: string;
  options: { value: string; label: string }[];
  minimum: number | null;
  maximum: number | null;
};

export type AppState = "DRAFT" | "ACTIVE" | "INACTIVE" | "ERROR";

export type AppInstall = {
  state: AppState;
  inputs: Record<string, unknown>;
  bindings: Record<string, number>;
  error: string | null;
  installed_by: string | null;
  installed_at: string;
  updated_at: string;
};

export type AppRun = {
  id: number;
  trigger: string;
  status: "OK" | "ERROR" | "SKIPPED";
  message: string | null;
  error: string | null;
  run_id: string | null;
  duration_ms: number;
  started_at: string;
};

export type AppSummary = {
  id: string;
  name: string;
  tagline: string;
  description: string;
  icon: string;
  category: string;
  publisher: string;
  version: string;
  inputs: AppField[];
  connections: { name: string; type: string; label: string }[];
  events: { name: string; label: string }[];
  timers: { name: string; label: string }[];
  run_now: string | null;
  install: AppInstall | null;
  last_run?: AppRun | null;
};

export type AppDetail = AppSummary & {
  runs: AppRun[];
  audit: { action: string; actor: string | null; detail: string | null; at: string }[];
};

export type SecretSpec = { name: string; label: string; help: string; required: boolean };

export type ConnectionType = {
  id: string;
  name: string;
  icon: string;
  description: string;
  fields: AppField[];
  secrets: SecretSpec[];
  setup_help: string;
};

export type Connection = {
  id: number;
  type: string;
  type_name: string;
  name: string;
  fields: Record<string, unknown>;
  secrets: {
    name: string;
    label: string;
    set: boolean;
    hint: string | null;
    updated_at: string | null;
    updated_by: string | null;
  }[];
  status: "UNTESTED" | "OK" | "ERROR";
  status_message: string | null;
  tested_at: string | null;
  used_by: string[];
  created_at: string;
  updated_at: string;
};

/** JSON call through the proxy, with the signed-in user as the actor (audit log). */
export async function appsCall<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api/backend${path}`, {
    method,
    headers: {
      accept: "application/json",
      "x-actor": ACTOR,
      ...(body === undefined ? {} : { "content-type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = (await res.json()) as { detail?: unknown };
      if (typeof data.detail === "string") detail = data.detail;
      else if (Array.isArray(data.detail)) detail = "Some values aren't valid";
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const STATE_LABEL: Record<AppState, string> = {
  DRAFT: "Set up",
  ACTIVE: "Active",
  INACTIVE: "Paused",
  ERROR: "Needs attention",
};

export const STATE_TONE = {
  DRAFT: "neutral",
  ACTIVE: "approved",
  INACTIVE: "hold",
  ERROR: "rejected",
} as const;
