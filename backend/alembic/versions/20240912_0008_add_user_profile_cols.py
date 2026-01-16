"""add user profile columns

Revision ID: 20240912_0008
Revises: 20240912_0007
Create Date: 2024-09-12 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20240912_0008"
down_revision = "20240912_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("first_name", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("surname", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "surname")
    op.drop_column("users", "first_name")
