"""Response models for the reference-data API."""

from datetime import date
from decimal import Decimal

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
