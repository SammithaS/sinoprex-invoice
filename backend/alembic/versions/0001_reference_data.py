"""Reference data: vendors, purchase orders, goods receipts, prior invoices; enable pgvector.

Revision ID: 0001
Revises:
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(14, 2)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "vendors",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("legal_name", sa.String(200), nullable=False),
        sa.Column("aliases", postgresql.JSONB(), nullable=False),
        sa.Column("gstin", sa.String(15), nullable=False, unique=True),
        sa.Column("state_code", sa.String(2), nullable=False),
        sa.Column("state", sa.String(60), nullable=False),
        sa.Column("pan", sa.String(10), nullable=False),
        sa.Column("bank_account_last4", sa.String(4), nullable=False),
        sa.Column("bank_ifsc", sa.String(11), nullable=False),
        sa.Column("payment_terms_days", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(60), nullable=False),
        sa.Column("email", sa.String(200), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
    )
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("po_number", sa.String(20), nullable=False, unique=True),
        sa.Column("vendor_id", sa.String(16), sa.ForeignKey("vendors.id"), nullable=False),
        sa.Column("po_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("subtotal", MONEY, nullable=False),
        sa.Column("tax_total", MONEY, nullable=False),
        sa.Column("total", MONEY, nullable=False),
        sa.Column("amount_invoiced", MONEY, nullable=False),
        sa.Column("requires_grn", sa.Boolean(), nullable=False),
        sa.Column("cost_centre", sa.String(20), nullable=False),
        sa.Column("buyer", sa.String(100), nullable=False),
    )
    op.create_table(
        "po_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "po_id", sa.Integer(), sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("hsn_sac", sa.String(8), nullable=False),
        sa.Column("qty", sa.Numeric(12, 3), nullable=False),
        sa.Column("uom", sa.String(10), nullable=False),
        sa.Column("unit_price", MONEY, nullable=False),
        sa.Column("tax_rate", sa.Numeric(5, 2), nullable=False),
    )
    op.create_table(
        "goods_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("grn_number", sa.String(20), nullable=False, unique=True),
        sa.Column("po_id", sa.Integer(), sa.ForeignKey("purchase_orders.id"), nullable=False),
        sa.Column("received_date", sa.Date(), nullable=False),
        sa.Column("received_by", sa.String(100), nullable=False),
    )
    op.create_table(
        "grn_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "grn_id", sa.Integer(), sa.ForeignKey("goods_receipts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("po_line_id", sa.Integer(), sa.ForeignKey("po_lines.id"), nullable=False),
        sa.Column("qty_received", sa.Numeric(12, 3), nullable=False),
    )
    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("vendor_id", sa.String(16), sa.ForeignKey("vendors.id"), nullable=False),
        sa.Column("invoice_number", sa.String(40), nullable=False),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("po_number", sa.String(20)),
        sa.Column("subtotal", MONEY, nullable=False),
        sa.Column("tax_total", MONEY, nullable=False),
        sa.Column("total", MONEY, nullable=False),
        sa.Column("file_hash", sa.String(64)),
        sa.Column("file_url", sa.String(500)),
        sa.Column("gl_account", sa.String(10)),
        sa.Column("cost_centre", sa.String(20)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("extracted", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_invoices_vendor_number", "invoices", ["vendor_id", "invoice_number"])
    op.create_index("ix_invoices_file_hash", "invoices", ["file_hash"])


def downgrade() -> None:
    op.drop_index("ix_invoices_file_hash", table_name="invoices")
    op.drop_index("ix_invoices_vendor_number", table_name="invoices")
    for table in ("invoices", "grn_lines", "goods_receipts", "po_lines", "purchase_orders", "vendors"):
        op.drop_table(table)
