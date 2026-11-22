"""Payments (RazorpayX payouts), PAID status on ERP documents, ERPNext document numbers.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-22
"""

import json
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("vendors", sa.Column("bank_account_number", sa.String(34), nullable=True))
    # Backfill the seed vendors' full account numbers (needed for payouts).
    seed = Path(__file__).resolve().parents[3] / "data" / "seed" / "vendors.json"
    if seed.exists():
        for v in json.loads(seed.read_text("utf-8")):
            if v.get("bank_account_number"):
                op.execute(
                    sa.text(
                        "UPDATE vendors SET bank_account_number = :n "
                        "WHERE id = :id AND bank_account_number IS NULL"
                    ).bindparams(n=v["bank_account_number"], id=v["id"])
                )

    # ERPNext names documents like ACC-PINV-2026-00001; leave room for longer series.
    op.alter_column("erp_documents", "doc_number", type_=sa.String(40))
    op.alter_column("erp_postings", "doc_number", type_=sa.String(40))
    op.add_column(
        "erp_documents",
        sa.Column("system", sa.String(20), nullable=False, server_default="mock"),
    )
    op.add_column("erp_documents", sa.Column("paid_at", sa.DateTime(), nullable=True))
    op.add_column("erp_documents", sa.Column("utr", sa.String(40), nullable=True))
    op.add_column("erp_documents", sa.Column("hold_released_by", sa.String(100), nullable=True))
    op.add_column("erp_documents", sa.Column("hold_released_at", sa.DateTime(), nullable=True))

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("doc_number", sa.String(40), nullable=False, index=True),
        sa.Column("run_id", sa.String(36), nullable=True, index=True),
        sa.Column("batch_id", sa.String(40), nullable=False, index=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("vendor_id", sa.String(16), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False, server_default="razorpayx"),
        sa.Column("status", sa.String(20), nullable=False, index=True),
        sa.Column("released_by", sa.String(100), nullable=True),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("contact_id", sa.String(40), nullable=True),
        sa.Column("fund_account_id", sa.String(40), nullable=True),
        sa.Column("payout_id", sa.String(40), nullable=True, unique=True),
        sa.Column("provider_status", sa.String(20), nullable=True),
        sa.Column("utr", sa.String(40), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_approved_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("doc_number", "attempt", name="uq_payments_doc_attempt"),
    )


def downgrade() -> None:
    op.drop_table("payments")
    for col in ("hold_released_at", "hold_released_by", "utr", "paid_at", "system"):
        op.drop_column("erp_documents", col)
    op.alter_column("erp_postings", "doc_number", type_=sa.String(20))
    op.alter_column("erp_documents", "doc_number", type_=sa.String(20))
    op.drop_column("vendors", "bank_account_number")
