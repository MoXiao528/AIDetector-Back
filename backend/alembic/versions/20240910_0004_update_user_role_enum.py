"""update user role enum and normalize values

Revision ID: 20240910_0004
Revises: 20240909_0003
Create Date: 2024-09-10 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20240910_0004"
down_revision = "20240909_0003"
branch_labels = None
depends_on = None


user_role_enum = postgresql.ENUM(
    "VISITOR",
    "INDIVIDUAL",
    "TEAM_ADMIN",
    "SYS_ADMIN",
    name="user_role",
    create_type=False,
)


def upgrade() -> None:
    # 先移除默认值，再转换类型，避免 text -> enum 的默认值冲突
    op.execute("ALTER TABLE users ALTER COLUMN role DROP DEFAULT")

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_role') THEN
                CREATE TYPE user_role AS ENUM ('VISITOR', 'INDIVIDUAL', 'TEAM_ADMIN', 'SYS_ADMIN');
            END IF;
        END
        $$;
        """
    )

    op.execute("UPDATE users SET role = 'SYS_ADMIN' WHERE role = 'ADMIN';")
    op.execute(
        """
        UPDATE users
        SET role = 'INDIVIDUAL'
        WHERE role NOT IN ('VISITOR', 'INDIVIDUAL', 'TEAM_ADMIN', 'SYS_ADMIN');
        """
    )

    op.alter_column(
        "users",
        "role",
        type_=user_role_enum,
        existing_type=sa.String(length=50),
        postgresql_using="role::user_role",
        existing_nullable=False,
    )
    op.execute("ALTER TABLE users ALTER COLUMN role SET DEFAULT 'INDIVIDUAL'")


def downgrade() -> None:
    op.execute("ALTER TABLE users ALTER COLUMN role DROP DEFAULT")
    op.alter_column(
        "users",
        "role",
        type_=sa.String(length=50),
        existing_type=user_role_enum,
        postgresql_using="role::text",
        existing_nullable=False,
    )
    op.execute("DROP TYPE IF EXISTS user_role")
    op.execute("ALTER TABLE users ALTER COLUMN role SET DEFAULT 'INDIVIDUAL'")
