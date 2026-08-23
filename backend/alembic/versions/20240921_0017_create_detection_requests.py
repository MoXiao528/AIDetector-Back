"""create detection requests table

Revision ID: 20240921_0017
Revises: 20240920_0016
Create Date: 2024-09-21 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20240921_0017"
down_revision = "20240920_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "detection_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'processing'"),
            nullable=False,
        ),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("reserved_chars", sa.Integer(), nullable=False),
        sa.Column("owner_token", sa.String(length=36), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "detection_id",
            sa.Integer(),
            sa.ForeignKey("detections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('processing', 'completed', 'failed')",
            name="ck_detection_requests_status",
        ),
        sa.CheckConstraint(
            "reserved_chars > 0",
            name="ck_detection_requests_reserved_chars_positive",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_type",
            "actor_id",
            "idempotency_key",
            name="uq_detection_requests_actor_key",
        ),
    )
    op.create_index(
        "uq_detection_requests_actor_processing",
        "detection_requests",
        ["actor_type", "actor_id"],
        unique=True,
        postgresql_where=sa.text("status = 'processing'"),
        sqlite_where=sa.text("status = 'processing'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_detection_requests_actor_processing",
        table_name="detection_requests",
    )
    op.drop_table("detection_requests")
