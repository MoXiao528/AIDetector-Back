"""remove username and enforce unique non-null name

Revision ID: 20240912_0007
Revises: 20240912_0006
Create Date: 2024-09-12 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20240912_0007"
down_revision = "20240912_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE users SET name = email WHERE name IS NULL OR name = ''")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "username")
    op.alter_column("users", "name", existing_type=sa.String(length=255), nullable=False)
    op.create_index("ix_users_name", "users", ["name"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_name", table_name="users")
    op.alter_column("users", "name", existing_type=sa.String(length=255), nullable=True)
    op.add_column("users", sa.Column("username", sa.String(length=150), nullable=True))
    op.create_index("ix_users_username", "users", ["username"], unique=True)
