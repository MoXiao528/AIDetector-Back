"""Daily quota usage ledger."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class QuotaUsage(Base):
    __tablename__ = "quota_usage"
    __table_args__ = (
        UniqueConstraint("actor_type", "actor_id", "usage_date", name="uq_quota_usage_actor_day"),
        Index("ix_quota_usage_actor_day", "actor_type", "actor_id", "usage_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    limit: Mapped[int] = mapped_column(Integer, nullable=False)
    used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
