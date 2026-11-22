"""SQLAlchemy models.

Reference data (vendors, purchase orders, goods receipts) and prior invoices; runs (one per
case), run steps (the live timeline), reviews and invoice history rows written by runs; the
operating procedure, exceptions, approvals, ERP postings, payments and corrections; and the apps
platform (connections with encrypted secrets, installs, run log, audit).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSONB on Postgres, plain JSON elsewhere (SQLite in unit tests).
JsonType = JSON().with_variant(JSONB(), "postgresql")
Money = Numeric(14, 2)


class Base(DeclarativeBase):
    pass


class Vendor(Base):
    __tablename__ = "vendors"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    legal_name: Mapped[str] = mapped_column(String(200))
    aliases: Mapped[list[str]] = mapped_column(JsonType, default=list)
    gstin: Mapped[str] = mapped_column(String(15), unique=True)
    state_code: Mapped[str] = mapped_column(String(2))
    state: Mapped[str] = mapped_column(String(60))
    pan: Mapped[str] = mapped_column(String(10))
    bank_account_last4: Mapped[str] = mapped_column(String(4))
    # Full account number for payouts; never returned by the API (only the last 4 digits are).
    bank_account_number: Mapped[str | None] = mapped_column(String(34))
    bank_ifsc: Mapped[str] = mapped_column(String(11))
    payment_terms_days: Mapped[int]
    category: Mapped[str] = mapped_column(String(60))
    email: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(10), default="ACTIVE")  # ACTIVE | BLOCKED

    purchase_orders: Mapped[list["PurchaseOrder"]] = relationship(back_populates="vendor")


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    po_number: Mapped[str] = mapped_column(String(20), unique=True)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendors.id"))
    po_date: Mapped[date]
    status: Mapped[str] = mapped_column(String(20))  # OPEN | PARTIALLY_INVOICED | CLOSED
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    subtotal: Mapped[Decimal] = mapped_column(Money)
    tax_total: Mapped[Decimal] = mapped_column(Money)
    total: Mapped[Decimal] = mapped_column(Money)
    amount_invoiced: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    requires_grn: Mapped[bool] = mapped_column(default=True)
    cost_centre: Mapped[str] = mapped_column(String(20))
    buyer: Mapped[str] = mapped_column(String(100))

    vendor: Mapped[Vendor] = relationship(back_populates="purchase_orders")
    lines: Mapped[list["PoLine"]] = relationship(
        back_populates="purchase_order", order_by="PoLine.line_no", cascade="all, delete-orphan"
    )
    receipts: Mapped[list["GoodsReceipt"]] = relationship(back_populates="purchase_order")


class PoLine(Base):
    __tablename__ = "po_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"))
    line_no: Mapped[int]
    description: Mapped[str] = mapped_column(String(300))
    hsn_sac: Mapped[str] = mapped_column(String(8))
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    uom: Mapped[str] = mapped_column(String(10))
    unit_price: Mapped[Decimal] = mapped_column(Money)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2))  # percent, e.g. 18.00

    purchase_order: Mapped[PurchaseOrder] = relationship(back_populates="lines")


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    grn_number: Mapped[str] = mapped_column(String(20), unique=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"))
    received_date: Mapped[date]
    received_by: Mapped[str] = mapped_column(String(100))

    purchase_order: Mapped[PurchaseOrder] = relationship(back_populates="receipts")
    lines: Mapped[list["GrnLine"]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan"
    )


class GrnLine(Base):
    __tablename__ = "grn_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    grn_id: Mapped[int] = mapped_column(ForeignKey("goods_receipts.id", ondelete="CASCADE"))
    po_line_id: Mapped[int] = mapped_column(ForeignKey("po_lines.id"))
    qty_received: Mapped[Decimal] = mapped_column(Numeric(12, 3))

    receipt: Mapped[GoodsReceipt] = relationship(back_populates="lines")
    po_line: Mapped[PoLine] = relationship()


class Invoice(Base):
    """An invoice the company has already seen: seeded history or a processed run."""

    __tablename__ = "invoices"
    __table_args__ = (Index("ix_invoices_vendor_number", "vendor_id", "invoice_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendors.id"))
    invoice_number: Mapped[str] = mapped_column(String(40))
    invoice_date: Mapped[date]
    po_number: Mapped[str | None] = mapped_column(String(20))
    subtotal: Mapped[Decimal] = mapped_column(Money)
    tax_total: Mapped[Decimal] = mapped_column(Money)
    total: Mapped[Decimal] = mapped_column(Money)
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    file_url: Mapped[str | None] = mapped_column(String(500))
    gl_account: Mapped[str | None] = mapped_column(String(10))
    cost_centre: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))  # POSTED | PAID | REJECTED ...
    source: Mapped[str] = mapped_column(String(10), default="SEED")  # SEED | RUN
    extracted: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # Rows registered by a run (duplicates / PO consumption for later cases).
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), unique=True)
    service_period: Mapped[str | None] = mapped_column(String(100))
    po_lines: Mapped[list[dict[str, Any]] | None] = mapped_column(JsonType)  # [{po_line_no, qty}]


class Run(Base):
    """One case: an invoice file going through the graph (LangGraph thread = run id)."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    # QUEUED | RUNNING | WAITING_REVIEW | COMPLETED | FAILED
    file_name: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(500))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    sample_key: Mapped[str | None] = mapped_column(String(60))
    source: Mapped[str] = mapped_column(String(10), default="UPLOAD")  # UPLOAD | SAMPLE | EMAIL
    # Email intake: "<message id>#<attachment name>", so a redelivered email makes no second run.
    intake_ref: Mapped[str | None] = mapped_column(String(300), unique=True)
    intake_from: Mapped[str | None] = mapped_column(String(200))
    rerun_of: Mapped[str | None] = mapped_column(String(36))
    decision: Mapped[str | None] = mapped_column(String(20))  # rules' decision (pre-human)
    final_decision: Mapped[str | None] = mapped_column(String(20))
    confidence: Mapped[float | None]
    vendor_id: Mapped[str | None] = mapped_column(String(16))
    vendor_name: Mapped[str | None] = mapped_column(String(200))
    invoice_number: Mapped[str | None] = mapped_column(String(40))
    total: Mapped[Decimal | None] = mapped_column(Money)
    procedure_version: Mapped[str | None] = mapped_column(String(20))
    explanation: Mapped[str | None]
    state: Mapped[dict[str, Any] | None] = mapped_column(JsonType)  # latest graph state snapshot
    review_request: Mapped[dict[str, Any] | None] = mapped_column(JsonType)  # interrupt payload
    error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    duration_ms: Mapped[int | None]

    steps: Mapped[list["RunStep"]] = relationship(
        back_populates="run", order_by="RunStep.seq", cascade="all, delete-orphan"
    )
    reviews: Mapped[list["Review"]] = relationship(
        back_populates="run", order_by="Review.id", cascade="all, delete-orphan"
    )


class RunStep(Base):
    """One timeline event of a run (node started/finished, rule result, pause, error)."""

    __tablename__ = "run_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int]
    type: Mapped[str] = mapped_column(String(30))
    node: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str | None] = mapped_column(String(20))
    message: Mapped[str | None]
    duration_ms: Mapped[int | None]
    data: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    run: Mapped[Run] = relationship(back_populates="steps")


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(20))  # APPROVE | REJECT | REQUEST_INFO
    comment: Mapped[str | None]
    reviewer: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    run: Mapped[Run] = relationship(back_populates="reviews")


# --- procedure, exceptions, approvals, mock ERP, corrections -----------------------------------


class Procedure(Base):
    """A version of the Operating Procedure (the `Policy` numbers), edited in /procedure."""

    __tablename__ = "procedures"

    version: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    policy: Mapped[dict[str, Any]] = mapped_column(JsonType)
    note: Mapped[str | None]
    created_by: Mapped[str | None] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CaseException(Base):
    """An exception handed to an owner with one specific question."""

    __tablename__ = "exceptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(30), index=True)
    rule_code: Mapped[str] = mapped_column(String(40))
    summary: Mapped[str]
    variance: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    citations: Mapped[list[str] | None] = mapped_column(JsonType)
    owner_role: Mapped[str] = mapped_column(String(80))
    owner_name: Mapped[str | None] = mapped_column(String(100))
    recommendation: Mapped[str | None]
    question: Mapped[str]
    attempted_steps: Mapped[list[str] | None] = mapped_column(JsonType)
    # OPEN | OVERRIDDEN (approved despite it) | REJECTED (case rejected) | RESOLVED
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    resolution: Mapped[str | None]
    resolved_by: Mapped[str | None] = mapped_column(String(100))
    resolved_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Approval(Base):
    """One approval a case needs: matrix, non-PO, bank change, or a four-eyes override."""

    __tablename__ = "approvals"
    __table_args__ = (Index("ix_approvals_run_level", "run_id", "level", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    level: Mapped[int]
    kind: Mapped[str] = mapped_column(String(20))  # MATRIX | NON_PO | BANK_CHANGE | OVERRIDE
    role: Mapped[str] = mapped_column(String(80))
    approver_name: Mapped[str | None] = mapped_column(String(100))  # who the procedure names
    reason: Mapped[str]
    citation: Mapped[str | None] = mapped_column(String(60))
    is_second_approver: Mapped[bool] = mapped_column(default=False)
    decision: Mapped[str] = mapped_column(
        String(10), default="PENDING"
    )  # PENDING|APPROVED|REJECTED
    decided_by: Mapped[str | None] = mapped_column(String(100))
    comment: Mapped[str | None]
    decided_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ErpDocument(Base):
    """A posted AP invoice document: the mock ERP's own record, or the local copy of a document
    posted to ERPNext (`system`)."""

    __tablename__ = "erp_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_number: Mapped[str] = mapped_column(String(40), unique=True)
    system: Mapped[str] = mapped_column(String(20), default="mock")  # mock | erpnext
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    vendor_id: Mapped[str] = mapped_column(String(16))
    invoice_number: Mapped[str] = mapped_column(String(40))
    invoice_date: Mapped[date]
    po_number: Mapped[str | None] = mapped_column(String(20))
    amount: Mapped[Decimal] = mapped_column(Money)
    tax_amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    gl_account: Mapped[str | None] = mapped_column(String(10))
    cost_centre: Mapped[str | None] = mapped_column(String(20))
    attachment_url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="POSTED")  # POSTED | PAID
    payload: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    paid_at: Mapped[datetime | None]
    utr: Mapped[str | None] = mapped_column(String(40))
    # A payment hold (bank change, …) released by a person after checking.
    hold_released_by: Mapped[str | None] = mapped_column(String(100))
    hold_released_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime | None]


class Payment(Base):
    """One payout attempt for a posted invoice (RazorpayX).

    A payment run only proposes (PROPOSED); nothing is sent until a person releases the batch
    (policy §1.3). A failed attempt stays as history; a retry is a new row with the next attempt
    number and a new idempotency key."""

    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("doc_number", "attempt", name="uq_payments_doc_attempt"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_number: Mapped[str] = mapped_column(String(40), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), index=True)  # the case
    batch_id: Mapped[str] = mapped_column(String(40), index=True)  # the payment run
    attempt: Mapped[int] = mapped_column(default=1)
    vendor_id: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    due_date: Mapped[date]
    mode: Mapped[str] = mapped_column(String(10))  # IMPS | NEFT | RTGS
    provider: Mapped[str] = mapped_column(String(20), default="razorpayx")
    # PROPOSED | CANCELLED | QUEUED (released, not confirmed sent) | PROCESSING | PAID | FAILED
    # | REVERSED
    status: Mapped[str] = mapped_column(String(20), index=True)
    released_by: Mapped[str | None] = mapped_column(String(100))
    released_at: Mapped[datetime | None]
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    contact_id: Mapped[str | None] = mapped_column(String(40))
    fund_account_id: Mapped[str | None] = mapped_column(String(40))
    payout_id: Mapped[str | None] = mapped_column(String(40), unique=True)
    provider_status: Mapped[str | None] = mapped_column(String(20))
    utr: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[str | None]
    retry_approved_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime | None]
    paid_at: Mapped[datetime | None]


class ErpPosting(Base):
    """One posting attempt by a run and its read-back verification."""

    __tablename__ = "erp_postings"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    doc_number: Mapped[str | None] = mapped_column(String(40))
    request: Mapped[dict[str, Any]] = mapped_column(JsonType)
    response: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    readback: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    verified: Mapped[bool | None]
    mismatches: Mapped[list[str] | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Correction(Base):
    """A reviewer's correction of an extracted field or coding proposal (feeds hints)."""

    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    vendor_id: Mapped[str | None] = mapped_column(String(16), index=True)
    field: Mapped[str] = mapped_column(String(40))
    old_value: Mapped[str | None]
    new_value: Mapped[str]
    reviewer: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Notification(Base):
    """An outbound n8n webhook call (review needed, vendor follow-up) and how it went."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(String(40))  # review_needed | vendor_followup
    status: Mapped[str] = mapped_column(String(20))  # SENT | FAILED | SKIPPED
    attempts: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None]
    payload: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


# --- Apps (installable automations) --------------------------------------------------------------


class AppConnection(Base):
    """Credentials an app uses (a Gmail account, a Slack webhook, an n8n instance).

    Plain settings live in `fields`; secret values live encrypted in `app_secrets` and are
    never returned by the API.
    """

    __tablename__ = "app_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(40), index=True)  # gmail | slack | n8n
    name: Mapped[str] = mapped_column(String(120))
    fields: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="UNTESTED")  # OK | ERROR | UNTESTED
    status_message: Mapped[str | None]
    tested_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime | None]

    secrets: Mapped[list["AppSecret"]] = relationship(
        back_populates="connection", cascade="all, delete-orphan"
    )


class AppSecret(Base):
    """One encrypted secret value (Fernet). `hint` is the last 4 characters, for the UI."""

    __tablename__ = "app_secrets"
    __table_args__ = (UniqueConstraint("connection_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("app_connections.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(60))
    ciphertext: Mapped[str]
    hint: Mapped[str | None] = mapped_column(String(8))
    updated_by: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())

    connection: Mapped[AppConnection] = relationship(back_populates="secrets")


class AppInstall(Base):
    """An installed app: its lifecycle state, settings and which connections it uses."""

    __tablename__ = "app_installs"

    app_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    state: Mapped[str] = mapped_column(String(20))  # DRAFT | ACTIVE | INACTIVE | ERROR
    inputs: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    bindings: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)  # req → connection id
    error: Mapped[str | None]
    runtime: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)  # timers, reminders
    installed_by: Mapped[str | None] = mapped_column(String(100))
    installed_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime | None]


class AppRun(Base):
    """One execution of an app handler (an event, a timer tick, or "Run now")."""

    __tablename__ = "app_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    app_id: Mapped[str] = mapped_column(String(60), index=True)
    trigger: Mapped[str] = mapped_column(String(60))  # event name, timer name, manual
    status: Mapped[str] = mapped_column(String(20))  # OK | ERROR | SKIPPED
    message: Mapped[str | None]
    error: Mapped[str | None]
    run_id: Mapped[str | None] = mapped_column(String(36))  # the case it was about, if any
    duration_ms: Mapped[int | None]
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AppAudit(Base):
    """Who installed / configured / activated what, and secret changes (never the values)."""

    __tablename__ = "app_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    app_id: Mapped[str | None] = mapped_column(String(60), index=True)
    connection_id: Mapped[int | None]
    action: Mapped[str] = mapped_column(String(60))
    actor: Mapped[str | None] = mapped_column(String(100))
    detail: Mapped[str | None]
    at: Mapped[datetime] = mapped_column(server_default=func.now())
