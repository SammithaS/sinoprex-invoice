"""Apps platform: connections with encrypted secrets, installs, run log, audit.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("type", sa.String(40), nullable=False, index=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("fields", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="UNTESTED"),
        sa.Column("status_message", sa.Text(), nullable=True),
        sa.Column("tested_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "app_secrets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "connection_id",
            sa.Integer(),
            sa.ForeignKey("app_connections.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("hint", sa.String(8), nullable=True),
        sa.Column("updated_by", sa.String(100), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("connection_id", "name"),
    )
    op.create_table(
        "app_installs",
        sa.Column("app_id", sa.String(60), primary_key=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("inputs", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("bindings", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("runtime", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("installed_by", sa.String(100), nullable=True),
        sa.Column("installed_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "app_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("app_id", sa.String(60), nullable=False, index=True),
        sa.Column("trigger", sa.String(60), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "app_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("app_id", sa.String(60), nullable=True, index=True),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("actor", sa.String(100), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    for table in ("app_audit", "app_runs", "app_installs", "app_secrets", "app_connections"):
        op.drop_table(table)
