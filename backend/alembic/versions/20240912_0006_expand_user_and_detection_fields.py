"""expand user and detection fields

Revision ID: 20240912_0006
Revises: 20240911_0005
Create Date: 2024-09-12 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20240912_0006"
down_revision = "20240911_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=150), nullable=True))
    op.add_column("users", sa.Column("name", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("organization", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("industry", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("job_role", sa.String(length=255), nullable=True))
    op.add_column(
        "users",
        sa.Column("plan_tier", sa.String(length=50), nullable=False, server_default=sa.text("'personal-free'")),
    )
    op.add_column(
        "users",
        sa.Column("credits_total", sa.Integer(), nullable=False, server_default=sa.text("10000")),
    )
    op.add_column(
        "users",
        sa.Column("credits_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "users",
        sa.Column("onboarding_completed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.add_column("detections", sa.Column("title", sa.String(length=255), nullable=True))
    op.add_column("detections", sa.Column("editor_html", sa.Text(), nullable=True))
    op.add_column("detections", sa.Column("functions_used", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("detections", "functions_used")
    op.drop_column("detections", "editor_html")
    op.drop_column("detections", "title")

    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "onboarding_completed")
    op.drop_column("users", "credits_used")
    op.drop_column("users", "credits_total")
    op.drop_column("users", "plan_tier")
    op.drop_column("users", "job_role")
    op.drop_column("users", "industry")
    op.drop_column("users", "organization")
    op.drop_column("users", "name")
    op.drop_column("users", "username")
