"""Scan history ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:  # pragma: no cover - 类型检查辅助
    from app.models.user import User


class ScanHistory(Base):
    __tablename__ = "scan_histories"
    __table_args__ = (
        Index("ix_scan_histories_user_id", "user_id"),
        Index("ix_scan_histories_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_content: Mapped[str] = mapped_column(Text, nullable=False)
    input_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    function_type: Mapped[str] = mapped_column(String(50), server_default="scan", nullable=False)
    result_label: Mapped[str] = mapped_column(String(50), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    analysis_result: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="scan_histories")
