"""create revoked access tokens table

Revision ID: 20240920_0016
Revises: 20240919_0015
Create Date: 2024-09-20 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20240920_0016"
down_revision = "20240919_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "revoked_access_tokens",
        sa.Column("jti", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("jti"),
    )
    op.create_index(
        "ix_revoked_access_tokens_expires_at",
        "revoked_access_tokens",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_revoked_access_tokens_expires_at", table_name="revoked_access_tokens")
    op.drop_table("revoked_access_tokens")
