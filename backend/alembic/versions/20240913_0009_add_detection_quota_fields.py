"""add detection quota fields

Revision ID: 20240913_0009
Revises: 20240912_0008
Create Date: 2024-09-13 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20240913_0009"
down_revision = "20240912_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("detections", sa.Column("actor_type", sa.String(length=20), nullable=True))
    op.add_column("detections", sa.Column("actor_id", sa.String(length=64), nullable=True))
    op.add_column(
        "detections",
        sa.Column("chars_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.alter_column("detections", "user_id", existing_type=sa.Integer(), nullable=True)

    op.execute("UPDATE detections SET actor_type = 'user' WHERE actor_type IS NULL")
    op.execute("UPDATE detections SET actor_id = CAST(user_id AS VARCHAR) WHERE actor_id IS NULL")

    op.alter_column(
        "detections",
        "actor_type",
        existing_type=sa.String(length=20),
        nullable=False,
        server_default=sa.text("'user'"),
    )
    op.alter_column("detections", "actor_id", existing_type=sa.String(length=64), nullable=False)

    op.create_index(
        "ix_detections_actor_type_actor_id",
        "detections",
        ["actor_type", "actor_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_detections_actor_type_actor_id", table_name="detections")
    op.alter_column("detections", "actor_id", existing_type=sa.String(length=64), nullable=True)
    op.alter_column(
        "detections",
        "actor_type",
        existing_type=sa.String(length=20),
        nullable=True,
        server_default=None,
    )
    op.alter_column("detections", "user_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("detections", "chars_used")
    op.drop_column("detections", "actor_id")
    op.drop_column("detections", "actor_type")
