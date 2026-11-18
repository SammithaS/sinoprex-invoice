"""Runs, run steps, reviews; invoice-history columns written by runs.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False, index=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False, index=True),
        sa.Column("sample_key", sa.String(60)),
        sa.Column("source", sa.String(10), nullable=False, server_default="UPLOAD"),
        sa.Column("rerun_of", sa.String(36)),
        sa.Column("decision", sa.String(20)),
        sa.Column("final_decision", sa.String(20)),
        sa.Column("confidence", sa.Float()),
        sa.Column("vendor_id", sa.String(16)),
        sa.Column("vendor_name", sa.String(200)),
        sa.Column("invoice_number", sa.String(40)),
        sa.Column("total", sa.Numeric(14, 2)),
        sa.Column("procedure_version", sa.String(20)),
        sa.Column("explanation", sa.String()),
        sa.Column("state", postgresql.JSONB()),
        sa.Column("review_request", postgresql.JSONB()),
        sa.Column("error", sa.String()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("duration_ms", sa.Integer()),
    )
    op.create_table(
        "run_steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("node", sa.String(40)),
        sa.Column("status", sa.String(20)),
        sa.Column("message", sa.String()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("data", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("comment", sa.String()),
        sa.Column("reviewer", sa.String(100)),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.add_column("invoices", sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id")))
    op.create_unique_constraint("uq_invoices_run_id", "invoices", ["run_id"])
    op.add_column("invoices", sa.Column("service_period", sa.String(100)))
    op.add_column("invoices", sa.Column("po_lines", postgresql.JSONB()))


def downgrade() -> None:
    op.drop_column("invoices", "po_lines")
    op.drop_column("invoices", "service_period")
    op.drop_constraint("uq_invoices_run_id", "invoices", type_="unique")
    op.drop_column("invoices", "run_id")
    op.drop_table("reviews")
    op.drop_table("run_steps")
    op.drop_table("runs")
