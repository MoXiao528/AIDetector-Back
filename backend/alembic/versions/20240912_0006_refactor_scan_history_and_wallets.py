"""refactor scan histories and add user profile/wallet tables

Revision ID: 20240912_0006
Revises: 20240911_0005
Create Date: 2024-09-12 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20240912_0006"
down_revision = "20240911_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Rename table
    op.rename_table("detections", "scan_histories")

    # 2. Rename columns
    op.alter_column("scan_histories", "input_text", new_column_name="input_content")
    op.alter_column("scan_histories", "meta_json", new_column_name="analysis_result")

    # 3. Add new columns to scan_histories
    op.add_column("scan_histories", sa.Column("title", sa.String(length=255), nullable=True))
    op.add_column("scan_histories", sa.Column("input_html", sa.Text(), nullable=True))
    op.add_column(
        "scan_histories",
        sa.Column("function_type", sa.String(length=50), server_default=sa.text("'scan'"), nullable=False),
    )

    # 4. Update users table
    op.add_column("users", sa.Column("username", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "users",
        sa.Column("status", sa.String(length=20), server_default=sa.text("'active'"), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    # 5. Create new tables
    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("first_name", sa.String(length=100), nullable=True),
        sa.Column("surname", sa.String(length=100), nullable=True),
        sa.Column("organization", sa.String(length=255), nullable=True),
        sa.Column("industry", sa.String(length=100), nullable=True),
        sa.Column("role", sa.String(length=50), nullable=True),
    )

    op.create_table(
        "credit_wallets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("total_quota", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("remaining_quota", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("reset_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_credit_wallets_user_id"),
    )

    op.create_table(
        "credit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(length=50), nullable=False),
        sa.Column("related_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("credit_logs")
    op.drop_table("credit_wallets")
    op.drop_table("user_profiles")

    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "status")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "username")

    op.drop_column("scan_histories", "function_type")
    op.drop_column("scan_histories", "input_html")
    op.drop_column("scan_histories", "title")

    op.alter_column("scan_histories", "analysis_result", new_column_name="meta_json")
    op.alter_column("scan_histories", "input_content", new_column_name="input_text")

    op.rename_table("scan_histories", "detections")
