"""Procedures, exceptions, approvals, mock ERP documents and postings, corrections.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(14, 2)


def _run_fk() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        "run_id",
        sa.String(36),
        sa.ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


def _created() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    op.create_table(
        "procedures",
        sa.Column("version", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("policy", postgresql.JSONB(), nullable=False),
        sa.Column("note", sa.String()),
        sa.Column("created_by", sa.String(100)),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false(), index=True),
        _created(),
    )
    op.create_table(
        "exceptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        _run_fk(),
        sa.Column("category", sa.String(30), nullable=False, index=True),
        sa.Column("rule_code", sa.String(40), nullable=False),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("variance", postgresql.JSONB()),
        sa.Column("citations", postgresql.JSONB()),
        sa.Column("owner_role", sa.String(80), nullable=False),
        sa.Column("owner_name", sa.String(100)),
        sa.Column("recommendation", sa.String()),
        sa.Column("question", sa.String(), nullable=False),
        sa.Column("attempted_steps", postgresql.JSONB()),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("resolution", sa.String()),
        sa.Column("resolved_by", sa.String(100)),
        sa.Column("resolved_at", sa.DateTime()),
        _created(),
    )
    op.create_table(
        "approvals",
        sa.Column("id", sa.Integer(), primary_key=True),
        _run_fk(),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("role", sa.String(80), nullable=False),
        sa.Column("approver_name", sa.String(100)),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("citation", sa.String(60)),
        sa.Column("is_second_approver", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("decision", sa.String(10), nullable=False, server_default="PENDING"),
        sa.Column("decided_by", sa.String(100)),
        sa.Column("comment", sa.String()),
        sa.Column("decided_at", sa.DateTime()),
        _created(),
    )
    op.create_index("ix_approvals_run_level", "approvals", ["run_id", "level"], unique=True)
    op.create_table(
        "erp_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("doc_number", sa.String(20), nullable=False, unique=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("vendor_id", sa.String(16), nullable=False),
        sa.Column("invoice_number", sa.String(40), nullable=False),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("po_number", sa.String(20)),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("tax_amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("gl_account", sa.String(10)),
        sa.Column("cost_centre", sa.String(20)),
        sa.Column("attachment_url", sa.String(500)),
        sa.Column("status", sa.String(20), nullable=False, server_default="POSTED"),
        sa.Column("payload", postgresql.JSONB()),
        _created(),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_table(
        "erp_postings",
        sa.Column("id", sa.Integer(), primary_key=True),
        _run_fk(),
        sa.Column("doc_number", sa.String(20)),
        sa.Column("request", postgresql.JSONB(), nullable=False),
        sa.Column("response", postgresql.JSONB()),
        sa.Column("readback", postgresql.JSONB()),
        sa.Column("verified", sa.Boolean()),
        sa.Column("mismatches", postgresql.JSONB()),
        _created(),
    )
    op.create_table(
        "corrections",
        sa.Column("id", sa.Integer(), primary_key=True),
        _run_fk(),
        sa.Column("vendor_id", sa.String(16), index=True),
        sa.Column("field", sa.String(40), nullable=False),
        sa.Column("old_value", sa.String()),
        sa.Column("new_value", sa.String(), nullable=False),
        sa.Column("reviewer", sa.String(100)),
        _created(),
    )


def downgrade() -> None:
    for table in (
        "corrections",
        "erp_postings",
        "erp_documents",
        "approvals",
        "exceptions",
        "procedures",
    ):
        op.drop_table(table)
