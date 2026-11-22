import type { components } from "./schema";

export type Schemas = components["schemas"];
export type Vendor = Schemas["VendorOut"];
export type PurchaseOrder = Schemas["PurchaseOrderOut"];
export type GoodsReceipt = Schemas["GoodsReceiptOut"];
export type Run = Schemas["RunOut"];
export type RunDetail = Schemas["RunDetailOut"];
export type RunStep = Schemas["RunStepOut"];
export type Sample = Schemas["SampleOut"];
export type Stats = Schemas["Stats"];
export type ReviewAction = Schemas["ReviewIn"]["action"];
export type Procedure = Schemas["ProcedureOut"];
export type Policy = Schemas["Policy-Output"];
export type CaseRecords = Schemas["CaseRecordsOut"];
export type CaseException = Schemas["ExceptionOut"];
export type Approval = Schemas["ApprovalOut"];
export type InboxItem = Schemas["InboxItem"];
export type LedgerRow = Schemas["LedgerRow"];
export type ChatTurn = Schemas["ChatTurn"];
export type ChatSource = Schemas["ChatSource"];
export type ChatHistory = Schemas["ChatHistoryOut"];
export type Integrations = Schemas["IntegrationsOut"];
export type EvalLatest = Schemas["EvalLatest"];
export type KnowledgeDoc = Schemas["KnowledgeDocOut"];
export type KnowledgeHit = Schemas["KnowledgeHitOut"];
export type ReindexOut = Schemas["ReindexOut"];
export type PaymentsOverview = Schemas["PaymentsOut"];
export type PayableItem = Schemas["QueueItemOut"];
export type Payment = Schemas["PaymentOut"];
export type PaymentBatch = Schemas["BatchOut"];

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

/** Calls FastAPI through the Next.js proxy at /api/backend. */
export async function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/backend${path}`, { ...init, headers: { accept: "application/json" } });
  return handle<T>(res);
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api/backend${path}`, {
    method: "POST",
    headers: { accept: "application/json", "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return handle<T>(res);
}

export async function apiUpload<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`/api/backend${path}`, { method: "POST", body: form });
  return handle<T>(res);
}

/** URL of a backend resource for <img>/<a> (goes through the proxy, so the key stays server-side). */
export function backendUrl(path: string): string {
  return `/api/backend${path}`;
}
