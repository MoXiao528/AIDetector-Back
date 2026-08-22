"""Revoked access-token identifiers."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class RevokedAccessToken(Base):
    __tablename__ = "revoked_access_tokens"
    __table_args__ = (Index("ix_revoked_access_tokens_expires_at", "expires_at"),)

    jti: Mapped[str] = mapped_column(String(36), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
