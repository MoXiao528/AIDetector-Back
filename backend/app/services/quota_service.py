from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.detection import Detection
from app.models.quota_usage import QuotaUsage

GUEST_DAILY_LIMIT = 5000
USER_DAILY_LIMIT = 30000


@dataclass(frozen=True)
class QuotaConsumeResult:
    limit: int
    used_today: int
    remaining: int


class QuotaExceededError(Exception):
    def __init__(self, *, limit: int, used_today: int, remaining: int) -> None:
        super().__init__("Daily quota exceeded")
        self.limit = limit
        self.used_today = used_today
        self.remaining = remaining


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
    usage_date = start_time.date()
    quota_total = db.scalar(
        select(QuotaUsage.used).where(
            QuotaUsage.actor_type == actor_type,
            QuotaUsage.actor_id == actor_id,
            QuotaUsage.usage_date == usage_date,
        )
    )
    if quota_total is not None:
        return int(quota_total or 0)

    detection_total = db.scalar(
        select(func.coalesce(func.sum(Detection.chars_used), 0)).where(
            Detection.actor_type == actor_type,
            Detection.actor_id == actor_id,
            Detection.created_at >= start_time,
            Detection.created_at < end_time,
        )
    )
    return int(detection_total or 0)


def _consume_quota_postgresql(
    db: Session,
    *,
    actor_type: str,
    actor_id: str,
    usage_date,
    chars: int,
    limit: int,
    baseline_used: int,
) -> QuotaConsumeResult:
    baseline = max(0, int(baseline_used or 0))
    if baseline + chars > limit:
        raise QuotaExceededError(limit=limit, used_today=baseline, remaining=max(limit - baseline, 0))

    insert_stmt = pg_insert(QuotaUsage).values(
        actor_type=actor_type,
        actor_id=actor_id,
        usage_date=usage_date,
        limit=limit,
        used=baseline + chars,
    )
    effective_used = func.greatest(QuotaUsage.used, baseline)
    stmt = (
        insert_stmt.on_conflict_do_update(
            index_elements=[QuotaUsage.actor_type, QuotaUsage.actor_id, QuotaUsage.usage_date],
            set_={
                "limit": limit,
                "used": effective_used + chars,
                "updated_at": func.now(),
            },
            where=(effective_used + chars <= limit),
        )
        .returning(QuotaUsage.used)
    )
    used_today = db.scalar(stmt)
    if used_today is None:
        current_used = db.scalar(
            select(QuotaUsage.used).where(
                QuotaUsage.actor_type == actor_type,
                QuotaUsage.actor_id == actor_id,
                QuotaUsage.usage_date == usage_date,
            )
        )
        used = int(current_used or 0)
        raise QuotaExceededError(limit=limit, used_today=used, remaining=max(limit - used, 0))

    used = int(used_today)
    return QuotaConsumeResult(limit=limit, used_today=used, remaining=max(limit - used, 0))


def _consume_quota_generic(
    db: Session,
    *,
    actor_type: str,
    actor_id: str,
    usage_date,
    chars: int,
    limit: int,
    baseline_used: int,
) -> QuotaConsumeResult:
    row = db.scalar(
        select(QuotaUsage)
        .where(
            QuotaUsage.actor_type == actor_type,
            QuotaUsage.actor_id == actor_id,
            QuotaUsage.usage_date == usage_date,
        )
        .with_for_update()
    )
    baseline = max(0, int(baseline_used or 0))
    if row is None:
        row = QuotaUsage(actor_type=actor_type, actor_id=actor_id, usage_date=usage_date, limit=limit, used=baseline)
        db.add(row)
        db.flush()

    row.limit = limit
    row.used = max(row.used, baseline)
    if row.used + chars > limit:
        raise QuotaExceededError(limit=limit, used_today=row.used, remaining=max(limit - row.used, 0))

    row.used += chars
    db.add(row)
    db.flush()
    return QuotaConsumeResult(limit=limit, used_today=row.used, remaining=max(limit - row.used, 0))


def consume_quota(
    db: Session,
    *,
    actor_type: str,
    actor_id: str,
    chars: int,
    start_time: datetime,
    limit: int,
    baseline_used: int = 0,
) -> QuotaConsumeResult:
    usage_date = start_time.date()
    dialect_name = db.get_bind().dialect.name
    if dialect_name == "postgresql":
        return _consume_quota_postgresql(
            db,
            actor_type=actor_type,
            actor_id=actor_id,
            usage_date=usage_date,
            chars=chars,
            limit=limit,
            baseline_used=baseline_used,
        )

    return _consume_quota_generic(
        db,
        actor_type=actor_type,
        actor_id=actor_id,
        usage_date=usage_date,
        chars=chars,
        limit=limit,
        baseline_used=baseline_used,
    )
