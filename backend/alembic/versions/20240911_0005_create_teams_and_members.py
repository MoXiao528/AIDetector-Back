"""create teams and team_members tables

Revision ID: 20240911_0005
Revises: 20240910_0004
Create Date: 2024-09-11 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20240911_0005"
down_revision = "20240910_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 幂等创建 PG enum type（只创建一次）
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'team_member_role') THEN
                CREATE TYPE team_member_role AS ENUM ('OWNER', 'ADMIN', 'MEMBER');
            END IF;
        END
        $$;
        """
    )

    # 2) 关键：使用 postgresql.ENUM + create_type=False，避免 SQLAlchemy 再次 CREATE TYPE
    team_member_role = postgresql.ENUM(
        "OWNER", "ADMIN", "MEMBER",
        name="team_member_role",
        create_type=False,
    )

    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_teams_name", "teams", ["name"], unique=True)

    op.create_table(
        "team_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", team_member_role, nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),
    )
    op.create_index("ix_team_members_team_id", "team_members", ["team_id"], unique=False)
    op.create_index("ix_team_members_user_id", "team_members", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_team_members_user_id", table_name="team_members")
    op.drop_index("ix_team_members_team_id", table_name="team_members")
    op.drop_table("team_members")

    op.drop_index("ix_teams_name", table_name="teams")
    op.drop_table("teams")

    # 注意：一般不建议在 downgrade 里 drop enum type（避免影响其他迁移/环境）
    # 如你确实需要彻底回滚并确认无其他依赖，可放开：
    # op.execute("DROP TYPE IF EXISTS team_member_role;")
