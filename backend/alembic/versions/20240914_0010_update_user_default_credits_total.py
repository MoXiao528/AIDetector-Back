"""update user default credits_total to 30000

Revision ID: 20240914_0010
Revises: 20240913_0009
Create Date: 2024-09-14 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20240914_0010"
down_revision = "20240913_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ALTER COLUMN credits_total SET DEFAULT 30000")


def downgrade() -> None:
    op.execute("ALTER TABLE users ALTER COLUMN credits_total SET DEFAULT 10000")
