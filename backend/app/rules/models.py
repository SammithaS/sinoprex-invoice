"""Inputs and outputs of the rules engine. Plain data — no I/O, no LLM.

Inputs: the extraction result, reference data (vendors, POs, GRNs, invoice history), the
vendor's contract terms (from RAG) and the policy numbers. Output: an `Assessment`
with PASS/WARN/FAIL rule results, exceptions, approvals and the decision.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Decision(StrEnum):
    APPROVED = "APPROVED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    ON_HOLD = "ON_HOLD"
    REJECTED = "REJECTED"


class RuleStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"  # not applicable to this invoice, or a dependency is missing


# What a non-passing rule does to the decision (policy precedence).
Severity = Literal["reject", "hold", "review"]


class ExceptionCategory(StrEnum):
    PRICE_VARIANCE = "PRICE_VARIANCE"
    QTY_VARIANCE = "QTY_VARIANCE"
    NO_PO = "NO_PO"
    NO_RECEIPT = "NO_RECEIPT"
    DUPLICATE_SUSPECT = "DUPLICATE_SUSPECT"
    BANK_CHANGE = "BANK_CHANGE"
    TAX_MISMATCH = "TAX_MISMATCH"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    OVER_PO_BALANCE = "OVER_PO_BALANCE"
    ERP_MISMATCH = "ERP_MISMATCH"  # read-back differs from the approved values


# --- reference data ----------------------------------------------------------------------------


class Vendor(BaseModel):
    id: str
    name: str
    legal_name: str
    aliases: list[str] = Field(default_factory=list)
    gstin: str
    state_code: str
    bank_account_last4: str | None = None
    bank_ifsc: str | None = None
    category: str | None = None
    email: str | None = None
    status: Literal["ACTIVE", "BLOCKED"] = "ACTIVE"


class POLine(BaseModel):
    line_no: int
    description: str
    hsn_sac: str | None = None
    qty: Decimal
    uom: str | None = None
    unit_price: Decimal
    tax_rate: Decimal


class PurchaseOrder(BaseModel):
    po_number: str
    vendor_id: str
    po_date: date
    status: str
    requires_grn: bool = True
    cost_centre: str | None = None
    buyer: str | None = None
    total: Decimal
    lines: list[POLine]


class GRNLine(BaseModel):
    line_no: int
    qty_received: Decimal


class GoodsReceipt(BaseModel):
    grn_number: str
    po_number: str
    received_date: date
    received_by: str | None = None
    lines: list[GRNLine]


class InvoicedLine(BaseModel):
    po_line_no: int
    qty: Decimal


class InvoiceRecord(BaseModel):
    """An invoice already registered: seed history or an earlier case."""

    ref: str  # case id or invoice number — what a reviewer is pointed to
    vendor_id: str
    invoice_number: str
    invoice_date: date
    total: Decimal
    po_number: str | None = None
    status: str = "POSTED"  # PAID | POSTED | APPROVED | NEEDS_REVIEW | ON_HOLD | REJECTED
    file_hash: str | None = None
    service_period: str | None = None
    gl_account: str | None = None
    cost_centre: str | None = None
    lines: list[InvoicedLine] | None = None  # quantities per PO line, when known


# Statuses that no longer count as a live invoice (for duplicates and PO consumption).
DEAD_STATUSES = frozenset({"REJECTED", "CANCELLED", "SUPERSEDED"})


class ContractRate(BaseModel):
    item: str
    hsn_sac: str | None = None
    uom: str | None = None
    rate: Decimal
    valid_from: date | None = None
    valid_to: date | None = None
    citation: str  # e.g. "contracts/technova §2.1"


class ContractTerms(BaseModel):
    """What RAG found in the vendor's contract. Only cited facts are used (policy §3.6)."""

    vendor_id: str
    source: str  # e.g. "contracts/technova"
    rates: list[ContractRate] = Field(default_factory=list)
    rate_revision_citation: str | None = None
    no_po_citation: str | None = None  # contract says no PO is raised (recurring service)
    payment_terms_days: int | None = None


class ReferenceData(BaseModel):
    company_gstin: str
    company_state_code: str
    vendors: list[Vendor]
    purchase_orders: list[PurchaseOrder]
    goods_receipts: list[GoodsReceipt]
    invoices: list[InvoiceRecord] = Field(default_factory=list)

    def vendor(self, vendor_id: str) -> Vendor | None:
        return next((v for v in self.vendors if v.id == vendor_id), None)

    def po(self, po_number: str) -> PurchaseOrder | None:
        key = po_number.strip().upper()
        return next((p for p in self.purchase_orders if p.po_number.upper() == key), None)

    def grns_for(self, po_number: str) -> list[GoodsReceipt]:
        return sorted(
            (g for g in self.goods_receipts if g.po_number == po_number),
            key=lambda g: g.received_date,
        )


class CaseContext(BaseModel):
    """Per-case facts the extraction can't supply."""

    case_ref: str | None = None
    file_hash: str | None = None
    received_on: date
    contract: ContractTerms | None = None
    # The vendor's contract sections by topic ('bank', 'tax', 'receipt', 'termination'), so a
    # failing non-price check also cites the clause that says the same (app/rag/clauses.py).
    contract_topics: dict[str, str] = Field(default_factory=dict)


# --- outputs -----------------------------------------------------------------------------------


class RuleResult(BaseModel):
    code: str  # stable id, e.g. "math.total"
    name: str
    status: RuleStatus
    message: str
    severity: Severity | None = None  # set when status is WARN/FAIL
    category: ExceptionCategory | None = None
    citations: list[str] = Field(default_factory=list)
    details: dict[str, str] = Field(default_factory=dict)


class ExceptionItem(BaseModel):
    """An exception handed to a named owner with one specific question."""

    category: ExceptionCategory
    rule_code: str
    summary: str
    variance: dict[str, str] = Field(default_factory=dict)
    citations: list[str] = Field(default_factory=list)
    owner_role: str
    owner_name: str | None = None
    recommendation: str
    question: str


class ApprovalRequirement(BaseModel):
    role: str
    name: str | None = None
    reason: str
    citation: str


class LineMatch(BaseModel):
    invoice_line_no: int
    po_line_no: int | None
    score: float
    reference_rate: Decimal | None = None
    reference_source: str | None = None  # citation of the contract, or the PO number


class POMatch(BaseModel):
    po_number: str
    inferred: bool
    score: float
    lines: list[LineMatch]


class ProposedCoding(BaseModel):
    gl_account: str
    cost_centre: str
    basis: str


class Assessment(BaseModel):
    decision: Decision
    rule_results: list[RuleResult]
    exceptions: list[ExceptionItem]
    approvals: list[ApprovalRequirement]
    vendor_id: str | None = None
    po_match: POMatch | None = None
    matched_grn: str | None = None
    po_cost_centre: str | None = None  # for ERP coding
    proposed_coding: ProposedCoding | None = None
    duplicate_of: str | None = None
    min_key_confidence: float
    touchless: dict[str, bool]  # touchless checklist (the ERP read-back is added by the graph)
    reasons: list[str]
    citations: list[str]

    @property
    def matched_po(self) -> str | None:
        return self.po_match.po_number if self.po_match else None
