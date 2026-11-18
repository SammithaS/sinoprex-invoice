/**
 * Shapes of the graph state the backend snapshots into `run.state` (plain JSON dumps of the
 * Pydantic models in backend/app/extraction/models.py and backend/app/rules/models.py).
 * Only the fields the UI reads are typed.
 */

import type { Decision } from "@/components/ui/badges";

export type RuleStatus = "PASS" | "WARN" | "FAIL" | "SKIP";

export type FieldEvidence = {
  value: string | null;
  raw?: string | null;
  confidence: number;
  page?: number | null;
  bbox?: [number, number, number, number] | null;
  snippet?: string | null;
  source: "text_layer" | "vision" | "derived" | "missing";
};

export type LineItem = {
  line_no: number;
  description?: string | null;
  hsn_sac?: string | null;
  qty?: string | null;
  uom?: string | null;
  unit_price?: string | null;
  tax_rate?: string | null;
  amount?: string | null;
};

export type Extraction = {
  data: {
    doc_type: string;
    vendor_name?: string | null;
    invoice_number?: string | null;
    invoice_date?: string | null;
    po_number?: string | null;
    total?: string | null;
    lines: LineItem[];
  };
  evidence: Record<string, FieldEvidence>;
  is_scanned: boolean;
  scan_quality?: number | null;
  model?: string | null;
  low_confidence: string[];
};

export type RuleResult = {
  code: string;
  name: string;
  status: RuleStatus;
  message: string;
  severity?: "reject" | "hold" | "review" | null;
  category?: string | null;
  citations: string[];
  details: Record<string, string>;
};

export type ExceptionItem = {
  category: string;
  rule_code: string;
  summary: string;
  variance: Record<string, string>;
  citations: string[];
  owner_role: string;
  owner_name?: string | null;
  recommendation: string;
  question: string;
};

export type Approval = { role: string; name?: string | null; reason: string; citation: string };

export type Assessment = {
  decision: Decision;
  rule_results: RuleResult[];
  exceptions: ExceptionItem[];
  approvals: Approval[];
  vendor_id?: string | null;
  po_match?: { po_number: string; inferred: boolean; score: number } | null;
  matched_grn?: string | null;
  proposed_coding?: { gl_account: string; cost_centre: string; basis: string } | null;
  duplicate_of?: string | null;
  min_key_confidence: number;
  touchless: Record<string, boolean>;
  reasons: string[];
  citations: string[];
};

export type Clause = { citation: string; heading: string; text: string };

export type CaseState = {
  document?: { page_count: number; is_scanned: boolean; quality?: number | null };
  extraction?: Extraction;
  assessment?: Assessment;
  clauses?: Clause[];
  explanation?: string | null;
  next_action?: string | null;
  final_decision?: string | null;
  reviews?: { action: string; comment?: string | null; reviewer?: string | null }[];
  errors?: string[];
};

export type ReviewRequest = {
  headline: string;
  decision: Decision;
  reasons: string[];
  exceptions: ExceptionItem[];
  approvals: Approval[];
  citations: string[];
  low_confidence_fields: { field: string; value: string | null; confidence: number; page?: number | null }[];
};

export function asDecision(value: string | null | undefined): Decision | null {
  return value === "APPROVED" || value === "NEEDS_REVIEW" || value === "ON_HOLD" || value === "REJECTED"
    ? value
    : null;
}

/** Field labels for the extracted-fields card, in display order. */
export const FIELD_LABELS: [string, string][] = [
  ["vendor_gstin", "Vendor GSTIN"],
  ["invoice_number", "Invoice no."],
  ["invoice_date", "Invoice date"],
  ["po_number", "PO reference"],
  ["subtotal", "Subtotal"],
  ["tax_total", "Tax"],
  ["total", "Grand total"],
  ["bank_account_number", "Bank account"],
];

export const TOUCHLESS_LABELS: Record<string, string> = {
  key_fields_confident: "Key fields read with confidence ≥ 0.8",
  supplier_verified: "Supplier, GSTIN and bank verified",
  matching_passed: "PO / receipt match within tolerance",
  no_duplicate_or_tax_exception: "No duplicate or tax exception",
  no_approval_gate: "No approval gate triggered",
};
