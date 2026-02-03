"""add detection ip address and allow nullable user_id

Revision ID: 20250118_0009
Revises: 20240912_0008
Create Date: 2025-01-18 00:09:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20250118_0009"
down_revision = "20240912_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("detections", sa.Column("ip_address", sa.String(length=45), nullable=True))
    op.create_index("ix_detections_ip_address", "detections", ["ip_address"], unique=False)
    op.alter_column("detections", "user_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.alter_column("detections", "user_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index("ix_detections_ip_address", table_name="detections")
    op.drop_column("detections", "ip_address")
