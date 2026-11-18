"""Normalised extraction output: typed invoice data plus per-field evidence."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.extraction.document import DocType

EvidenceSource = Literal["text_layer", "vision", "derived", "missing"]

# Fields that must be present for an invoice to be processed (GST Rule 46 + AP policy §2.3).
REQUIRED_FIELDS = ("vendor_name", "vendor_gstin", "invoice_number", "invoice_date", "total")
# Fields whose confidence gates touchless processing (AP policy §9.1).
KEY_FIELDS = (
    "vendor_gstin",
    "invoice_number",
    "invoice_date",
    "po_number",
    "subtotal",
    "tax_total",
    "total",
    "bank_account_number",
)


class FieldEvidence(BaseModel):
    """Where a value came from and how much we trust it."""

    value: str | None = Field(description="Normalised value as a string (what the UI shows).")
    raw: str | None = Field(default=None, description="As read from the document.")
    confidence: float = Field(ge=0, le=1)
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = Field(
        default=None, description="x0, y0, x1, y1 in PDF points (origin top-left)."
    )
    snippet: str | None = None
    source: EvidenceSource = "missing"
    notes: list[str] = Field(default_factory=list)


class LineItem(BaseModel):
    line_no: int
    description: str | None = None
    hsn_sac: str | None = None
    qty: Decimal | None = None
    uom: str | None = None
    unit_price: Decimal | None = None
    tax_rate: Decimal | None = None
    amount: Decimal | None = None
    confidence: float = 0.0
    page: int | None = None


class InvoiceData(BaseModel):
    """Canonical invoice fields used by the rules engine."""

    doc_type: DocType
    vendor_name: str | None = None
    vendor_gstin: str | None = None
    vendor_address: str | None = None
    buyer_name: str | None = None
    buyer_gstin: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    po_number: str | None = None
    place_of_supply: str | None = None  # 2-digit state code
    currency: str = "INR"
    service_period: str | None = None
    original_invoice_ref: str | None = None
    bank_name: str | None = None
    bank_account_number: str | None = None
    bank_ifsc: str | None = None
    lines: list[LineItem] = Field(default_factory=list)
    subtotal: Decimal | None = None
    cgst: Decimal | None = None
    sgst: Decimal | None = None
    igst: Decimal | None = None
    tax_total: Decimal | None = None
    total: Decimal | None = None


class ExtractionResult(BaseModel):
    data: InvoiceData
    evidence: dict[str, FieldEvidence]
    is_scanned: bool
    scan_quality: float | None = None
    doc_type_source: Literal["text_layer", "llm", "both"]
    model: str | None = None  # which model actually answered
    attempts: int = 1
    missing_required: list[str] = Field(default_factory=list)
    low_confidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0

    @property
    def min_key_confidence(self) -> float:
        present = [
            self.evidence[f].confidence
            for f in KEY_FIELDS
            if f in self.evidence and self.evidence[f].value is not None
        ]
        return min(present, default=0.0)
