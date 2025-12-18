"""create detections table

Revision ID: 20240909_0003
Revises: 20240909_0002
Create Date: 2024-09-09 02:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20240909_0003"
down_revision = "20240909_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "detections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("result_label", sa.String(length=50), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("meta_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_detections_user_id", "detections", ["user_id"], unique=False)
    op.create_index("ix_detections_created_at", "detections", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_detections_created_at", table_name="detections")
    op.drop_index("ix_detections_user_id", table_name="detections")
    op.drop_table("detections")
