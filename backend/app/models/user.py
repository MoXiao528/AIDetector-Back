from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Enum as SqlEnum

from app.core.roles import UserRole
from app.db.base_class import Base

if TYPE_CHECKING:  # pragma: no cover - 类型检查辅助
    from app.models.api_key import APIKey
    from app.models.scan_history import ScanHistory
    from app.models.user_profile import UserProfile
    from app.models.wallet import CreditLog, CreditWallet
    from app.models.team import TeamMember


class User(Base):
    """用户表模型，邮箱唯一。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SqlEnum(UserRole, name="user_role", values_callable=lambda enum: [e.value for e in enum]),
        server_default=UserRole.INDIVIDUAL.value,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
    status: Mapped[str] = mapped_column(String(20), server_default="active", nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    api_keys: Mapped[list["APIKey"]] = relationship("APIKey", back_populates="user", cascade="all, delete-orphan")
    scan_histories: Mapped[list["ScanHistory"]] = relationship(
        "ScanHistory", back_populates="user", cascade="all, delete-orphan"
    )
    team_memberships: Mapped[list["TeamMember"]] = relationship(
        "TeamMember", back_populates="user", cascade="all, delete-orphan"
    )
    profile: Mapped["UserProfile | None"] = relationship(
        "UserProfile", back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    credit_wallet: Mapped["CreditWallet | None"] = relationship(
        "CreditWallet", back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    credit_logs: Mapped[list["CreditLog"]] = relationship(
        "CreditLog", back_populates="user", cascade="all, delete-orphan"
    )
