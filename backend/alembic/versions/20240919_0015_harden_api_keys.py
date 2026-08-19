"""harden api key lifecycle

Revision ID: 20240919_0015
Revises: 20240918_0014
Create Date: 2024-09-19 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20240919_0015"
down_revision = "20240918_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("api_keys", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))

    op.execute(
        """
        WITH ranked_active AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY user_id
                    ORDER BY created_at DESC, id DESC
                ) AS active_rank
            FROM api_keys
            WHERE status = 'active'
        )
        UPDATE api_keys AS target
        SET
            status = 'inactive',
            expires_at = CURRENT_TIMESTAMP,
            revoked_at = CURRENT_TIMESTAMP
        FROM ranked_active
        WHERE target.id = ranked_active.id
          AND ranked_active.active_rank > 5
        """
    )
    op.execute(
        """
        UPDATE api_keys
        SET
            expires_at = CASE
                WHEN status = 'active' THEN CURRENT_TIMESTAMP + INTERVAL '30 days'
                ELSE CURRENT_TIMESTAMP
            END,
            revoked_at = CASE
                WHEN status = 'inactive' THEN COALESCE(revoked_at, CURRENT_TIMESTAMP)
                ELSE revoked_at
            END
        WHERE expires_at IS NULL
        """
    )
    op.alter_column(
        "api_keys",
        "expires_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("api_keys", "revoked_at")
    op.drop_column("api_keys", "expires_at")
