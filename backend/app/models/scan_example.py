"""Scan example ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class ScanExample(Base):
    __tablename__ = "scan_examples"
    __table_args__ = (
        UniqueConstraint("locale", "placement", "key", name="uq_scan_examples_locale_placement_key"),
        Index("ix_scan_examples_locale_placement", "locale", "placement"),
        Index("ix_scan_examples_is_active_sort_order", "is_active", "sort_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False)
    placement: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    doc_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    length_label: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ai: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mixed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    human: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
