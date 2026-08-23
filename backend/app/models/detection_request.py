"""Idempotent detection request admission state."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class DetectionRequest(Base):
    __tablename__ = "detection_requests"
    __table_args__ = (
        UniqueConstraint(
            "actor_type",
            "actor_id",
            "idempotency_key",
            name="uq_detection_requests_actor_key",
        ),
        CheckConstraint(
            "status IN ('processing', 'completed', 'failed')",
            name="ck_detection_requests_status",
        ),
        CheckConstraint(
            "reserved_chars > 0",
            name="ck_detection_requests_reserved_chars_positive",
        ),
        Index(
            "uq_detection_requests_actor_processing",
            "actor_type",
            "actor_id",
            unique=True,
            postgresql_where=text("status = 'processing'"),
            sqlite_where=text("status = 'processing'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="processing")
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    reserved_chars: Mapped[int] = mapped_column(Integer, nullable=False)
    owner_token: Mapped[str] = mapped_column(String(36), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detection_id: Mapped[int | None] = mapped_column(
        ForeignKey("detections.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
