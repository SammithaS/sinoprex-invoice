"""SQLAlchemy models.

Phase 1 covers reference data (vendors, purchase orders, goods receipts) and prior
invoices for duplicate detection. Run, case, exception, approval and ERP tables arrive
in later phases with their own migrations.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Numeric, String, func
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
