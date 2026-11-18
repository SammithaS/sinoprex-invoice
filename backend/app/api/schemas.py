"""Response models for the reference-data API."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class _Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class VendorOut(_Orm):
    id: str
    name: str
    legal_name: str
    aliases: list[str]
    gstin: str
    state_code: str
    state: str
    pan: str
    bank_account_last4: str
    payment_terms_days: int
    category: str
    status: str


class PoLineOut(_Orm):
    line_no: int
    description: str
    hsn_sac: str
    qty: Decimal
    uom: str
    unit_price: Decimal
    tax_rate: Decimal


class PurchaseOrderOut(_Orm):
    po_number: str
    vendor_id: str
    vendor_name: str
    po_date: date
    status: str
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    amount_invoiced: Decimal
    remaining: Decimal
    requires_grn: bool
    cost_centre: str
    buyer: str
    lines: list[PoLineOut]


class GrnLineOut(BaseModel):
    po_line_no: int
    description: str
    qty_received: Decimal


class GoodsReceiptOut(BaseModel):
    grn_number: str
    po_number: str
    vendor_name: str
    received_date: date
    received_by: str
    lines: list[GrnLineOut]


class HealthOut(BaseModel):
    status: str
    env: str
    database: str


# --- knowledge base (Phase 5) ------------------------------------------------------------------


class KnowledgeDocOut(BaseModel):
    source: str
    title: str
    doc_type: str
    vendor_id: str | None
    chunks: int
    indexed_chunks: int


class KnowledgeHitOut(BaseModel):
    citation: str
    heading: str
    doc_type: str
    vendor_id: str | None
    text: str
    score: float
    dense_similarity: float | None


class KnowledgeSearchIn(BaseModel):
    query: str
    vendor_id: str | None = None
    doc_types: list[str] = []
    k: int = 5


class ReindexOut(BaseModel):
    chunks: int
    added: int
    removed: int
    unchanged: int
    by_source: dict[str, int]


# --- runs (Phase 6) ------------------------------------------------------------------------------


class RunOut(_Orm):
    id: str
    status: str
    file_name: str
    file_hash: str
    source: str
    sample_key: str | None
    rerun_of: str | None
    decision: str | None
    final_decision: str | None
    confidence: float | None
    vendor_id: str | None
    vendor_name: str | None
    invoice_number: str | None
    total: Decimal | None
    procedure_version: str | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None


class RunStepOut(_Orm):
    seq: int
    type: str
    node: str | None
    status: str | None
    message: str | None
    duration_ms: int | None
    data: dict[str, Any] | None
    created_at: datetime


class ReviewOut(_Orm):
    action: str
    comment: str | None
    reviewer: str | None
    created_at: datetime


class RunDetailOut(RunOut):
    explanation: str | None
    state: dict[str, Any] | None
    review_request: dict[str, Any] | None
    steps: list[RunStepOut]
    reviews: list[ReviewOut]


class ReviewIn(BaseModel):
    action: Literal["APPROVE", "REJECT", "REQUEST_INFO"]
    comment: str | None = None
    reviewer: str | None = None


class SampleOut(BaseModel):
    key: str
    title: str
    edge_case: str
    is_scanned: bool
    expected_decision: str
    depends_on: list[str]
