"""Detection result ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.db.types import JSONType

if TYPE_CHECKING:  # pragma: no cover - 类型检查辅助
    from app.models.user import User


class Detection(Base):
    __tablename__ = "detections"
    __table_args__ = (
        Index("ix_detections_user_id", "user_id"),
        Index("ix_detections_created_at", "created_at"),
        Index("ix_detections_actor_type_actor_id", "actor_type", "actor_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="user")
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chars_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    editor_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    functions_used: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    result_label: Mapped[str] = mapped_column(String(50), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    meta_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="detections")
