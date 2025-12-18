"""create api_keys table

Revision ID: 20240909_0002
Revises: 20240909_0001
Create Date: 2024-09-09 01:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20240909_0002"
down_revision = "20240909_0001"
branch_labels = None
depends_on = None


api_key_status_enum = postgresql.ENUM(
    "active",
    "inactive",
    name="api_key_status",
    create_type=False,
)


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'api_key_status') THEN
                CREATE TYPE api_key_status AS ENUM ('active', 'inactive');
            END IF;
        END
        $$;
        """
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("key_hash", sa.String(length=255), nullable=False, unique=True),
        sa.Column("status", api_key_status_enum, nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
    )
    op.create_index(op.f("ix_api_keys_user_id"), "api_keys", ["user_id"], unique=False)
    op.create_index(op.f("ix_api_keys_status"), "api_keys", ["status"], unique=False)
    op.create_index(op.f("ix_api_keys_created_at"), "api_keys", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_api_keys_created_at"), table_name="api_keys")
    op.drop_index(op.f("ix_api_keys_status"), table_name="api_keys")
    op.drop_index(op.f("ix_api_keys_user_id"), table_name="api_keys")
    op.drop_table("api_keys")
    op.execute("DROP TYPE IF EXISTS api_key_status")
