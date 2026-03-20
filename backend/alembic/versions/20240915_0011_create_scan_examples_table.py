"""create scan examples table

Revision ID: 20240915_0011
Revises: 20240914_0010
Create Date: 2024-09-15 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20240915_0011"
down_revision = "20240914_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scan_examples",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("placement", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("doc_type", sa.String(length=50), nullable=True),
        sa.Column("length_label", sa.String(length=50), nullable=True),
        sa.Column("ai", sa.Integer(), nullable=True),
        sa.Column("mixed", sa.Integer(), nullable=True),
        sa.Column("human", sa.Integer(), nullable=True),
        sa.Column("snapshot", sa.String(length=255), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("locale", "placement", "key", name="uq_scan_examples_locale_placement_key"),
    )
    op.create_index(
        "ix_scan_examples_locale_placement",
        "scan_examples",
        ["locale", "placement"],
        unique=False,
    )
    op.create_index(
        "ix_scan_examples_is_active_sort_order",
        "scan_examples",
        ["is_active", "sort_order"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_scan_examples_is_active_sort_order", table_name="scan_examples")
    op.drop_index("ix_scan_examples_locale_placement", table_name="scan_examples")
    op.drop_table("scan_examples")
