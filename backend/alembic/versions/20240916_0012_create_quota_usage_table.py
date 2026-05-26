"""create quota usage table

Revision ID: 20240916_0012
Revises: 20240915_0011
Create Date: 2024-09-16 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20240916_0012"
down_revision = "20240915_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quota_usage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("limit", sa.Integer(), nullable=False),
        sa.Column("used", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("actor_type", "actor_id", "usage_date", name="uq_quota_usage_actor_day"),
    )
    op.create_index(
        "ix_quota_usage_actor_day",
        "quota_usage",
        ["actor_type", "actor_id", "usage_date"],
        unique=False,
    )
    op.execute(
        """
        INSERT INTO quota_usage (actor_type, actor_id, usage_date, "limit", used)
        SELECT
            actor_type,
            actor_id,
            CAST(created_at AS DATE) AS usage_date,
            CASE WHEN actor_type = 'guest' THEN 5000 ELSE 30000 END AS "limit",
            COALESCE(SUM(chars_used), 0) AS used
        FROM detections
        GROUP BY actor_type, actor_id, CAST(created_at AS DATE)
        """
    )


def downgrade() -> None:
    op.drop_index("ix_quota_usage_actor_day", table_name="quota_usage")
    op.drop_table("quota_usage")
