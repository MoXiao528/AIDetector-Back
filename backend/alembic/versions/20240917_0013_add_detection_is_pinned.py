"""add detection pinned flag

Revision ID: 20240917_0013
Revises: 20240916_0012
Create Date: 2024-09-17 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20240917_0013"
down_revision = "20240916_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "detections",
        sa.Column("is_pinned", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_index(
        "ix_detections_user_pinned_created",
        "detections",
        ["user_id", "is_pinned", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_detections_user_pinned_created", table_name="detections")
    op.drop_column("detections", "is_pinned")
