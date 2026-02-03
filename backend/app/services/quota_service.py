from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.detection import Detection

GUEST_DAILY_LIMIT = 5000
USER_DAILY_LIMIT = 30000


def get_quota_limit(actor_type: str) -> int:
    return GUEST_DAILY_LIMIT if actor_type == "guest" else USER_DAILY_LIMIT


def get_today_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = now or datetime.now(timezone.utc)
    start = datetime(current.year, current.month, current.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return start, end


def get_used_today(
    db: Session,
    actor_type: str,
    actor_id: str,
    start_time: datetime,
    end_time: datetime,
) -> int:
    total = db.scalar(
        select(func.coalesce(func.sum(Detection.chars_used), 0)).where(
            Detection.actor_type == actor_type,
            Detection.actor_id == actor_id,
            Detection.created_at >= start_time,
            Detection.created_at < end_time,
        )
    )
    return int(total or 0)
