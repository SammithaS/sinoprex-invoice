import type { components } from "./schema";

export type Schemas = components["schemas"];
export type Vendor = Schemas["VendorOut"];
export type PurchaseOrder = Schemas["PurchaseOrderOut"];
export type GoodsReceipt = Schemas["GoodsReceiptOut"];

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/** Calls FastAPI through the Next.js proxy at /api/backend. */
export async function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/backend${path}`, { ...init, headers: { accept: "application/json" } });
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
