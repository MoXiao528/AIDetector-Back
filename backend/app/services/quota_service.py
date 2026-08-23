from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from math import ceil
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.detection import Detection
from app.models.detection_request import DetectionRequest as DetectionRequestRecord
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


class IdempotencyKeyConflictError(Exception):
    pass


class DetectionRequestInProgressError(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Detection request is already in progress")
        self.retry_after = max(1, retry_after)


class DetectionActorBusyError(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Actor already has a detection in progress")
        self.retry_after = max(1, retry_after)


class DetectionReservationLostError(Exception):
    pass


@dataclass(frozen=True)
class DetectionAdmission:
    state: str
    request_id: int
    actor_type: str
    actor_id: str
    idempotency_key: str
    owner_token: str
    usage_date: date
    reserved_chars: int
    detection_id: int | None = None


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


def get_effective_used_today(
    db: Session,
    actor_type: str,
    actor_id: str,
    start_time: datetime,
    end_time: datetime,
) -> int:
    committed = get_used_today(
        db,
        actor_type=actor_type,
        actor_id=actor_id,
        start_time=start_time,
        end_time=end_time,
    )
    reserved = db.scalar(
        select(func.coalesce(func.sum(DetectionRequestRecord.reserved_chars), 0)).where(
            DetectionRequestRecord.actor_type == actor_type,
            DetectionRequestRecord.actor_id == actor_id,
            DetectionRequestRecord.usage_date == start_time.date(),
            DetectionRequestRecord.status == "processing",
            DetectionRequestRecord.lease_expires_at > func.now(),
        )
    )
    return committed + int(reserved or 0)


def _database_now(db: Session) -> datetime:
    current = db.scalar(select(func.now())) or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _lease_retry_after(lease_expires_at: datetime, current: datetime) -> int:
    lease = lease_expires_at
    if lease.tzinfo is None:
        lease = lease.replace(tzinfo=timezone.utc)
    return max(1, ceil((lease - current).total_seconds()))


def _lease_is_active(lease_expires_at: datetime, current: datetime) -> bool:
    lease = lease_expires_at
    if lease.tzinfo is None:
        lease = lease.replace(tzinfo=timezone.utc)
    return lease > current


def _lock_quota_usage(
    db: Session,
    *,
    actor_type: str,
    actor_id: str,
    usage_date: date,
    limit: int,
    baseline_used: int,
) -> QuotaUsage:
    dialect_name = db.get_bind().dialect.name
    baseline = max(0, int(baseline_used or 0))
    lookup = (
        select(QuotaUsage)
        .where(
            QuotaUsage.actor_type == actor_type,
            QuotaUsage.actor_id == actor_id,
            QuotaUsage.usage_date == usage_date,
        )
        .with_for_update()
    )

    if dialect_name == "postgresql":
        db.execute(
            pg_insert(QuotaUsage)
            .values(
                actor_type=actor_type,
                actor_id=actor_id,
                usage_date=usage_date,
                limit=limit,
                used=baseline,
            )
            .on_conflict_do_nothing(
                index_elements=[QuotaUsage.actor_type, QuotaUsage.actor_id, QuotaUsage.usage_date]
            )
        )
        row = db.scalar(lookup)
    else:
        row = db.scalar(lookup)
        if row is None:
            row = QuotaUsage(
                actor_type=actor_type,
                actor_id=actor_id,
                usage_date=usage_date,
                limit=limit,
                used=baseline,
            )
            db.add(row)
            db.flush()

    if row is None:  # pragma: no cover - defensive guard after PostgreSQL UPSERT
        raise RuntimeError("Unable to lock quota usage row")
    row.limit = limit
    return row


def reserve_detection_request(
    db: Session,
    *,
    actor_type: str,
    actor_id: str,
    idempotency_key: str,
    request_hash: str,
    chars: int,
    limit: int,
    lease_seconds: int,
) -> DetectionAdmission:
    current = _database_now(db)
    day_start, day_end = get_today_bounds(current)
    baseline_used = get_used_today(
        db,
        actor_type=actor_type,
        actor_id=actor_id,
        start_time=day_start,
        end_time=day_end,
    )
    quota_row = _lock_quota_usage(
        db,
        actor_type=actor_type,
        actor_id=actor_id,
        usage_date=current.date(),
        limit=limit,
        baseline_used=baseline_used,
    )

    key_record = db.scalar(
        select(DetectionRequestRecord)
        .where(
            DetectionRequestRecord.actor_type == actor_type,
            DetectionRequestRecord.actor_id == actor_id,
            DetectionRequestRecord.idempotency_key == idempotency_key,
        )
        .with_for_update()
    )
    if key_record is not None and key_record.request_hash != request_hash:
        raise IdempotencyKeyConflictError
    if key_record is not None and key_record.status == "completed":
        return DetectionAdmission(
            state="completed",
            request_id=key_record.id,
            actor_type=actor_type,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            owner_token=key_record.owner_token,
            usage_date=key_record.usage_date,
            reserved_chars=key_record.reserved_chars,
            detection_id=key_record.detection_id,
        )

    active_record = db.scalar(
        select(DetectionRequestRecord)
        .where(
            DetectionRequestRecord.actor_type == actor_type,
            DetectionRequestRecord.actor_id == actor_id,
            DetectionRequestRecord.status == "processing",
        )
        .with_for_update()
    )
    if active_record is not None and _lease_is_active(active_record.lease_expires_at, current):
        retry_after = _lease_retry_after(active_record.lease_expires_at, current)
        if key_record is not None and active_record.id == key_record.id:
            raise DetectionRequestInProgressError(retry_after)
        raise DetectionActorBusyError(retry_after)

    if active_record is not None:
        active_record.status = "failed"
        active_record.owner_token = str(uuid4())
        active_record.lease_expires_at = current
        db.flush()

    used = int(quota_row.used or 0)
    if used + chars > limit:
        raise QuotaExceededError(limit=limit, used_today=used, remaining=max(limit - used, 0))

    owner_token = str(uuid4())
    lease_expires_at = current + timedelta(seconds=max(1, lease_seconds))
    if key_record is None:
        key_record = DetectionRequestRecord(
            actor_type=actor_type,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            status="processing",
            usage_date=current.date(),
            reserved_chars=chars,
            owner_token=owner_token,
            lease_expires_at=lease_expires_at,
        )
        db.add(key_record)
    else:
        key_record.status = "processing"
        key_record.usage_date = current.date()
        key_record.reserved_chars = chars
        key_record.owner_token = owner_token
        key_record.lease_expires_at = lease_expires_at
        key_record.detection_id = None
    db.flush()

    return DetectionAdmission(
        state="acquired",
        request_id=key_record.id,
        actor_type=actor_type,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        owner_token=owner_token,
        usage_date=current.date(),
        reserved_chars=chars,
    )


def lock_detection_settlement(
    db: Session,
    *,
    admission: DetectionAdmission,
    limit: int,
) -> tuple[DetectionRequestRecord, QuotaUsage]:
    quota_row = db.scalar(
        select(QuotaUsage)
        .where(
            QuotaUsage.actor_type == admission.actor_type,
            QuotaUsage.actor_id == admission.actor_id,
            QuotaUsage.usage_date == admission.usage_date,
        )
        .with_for_update()
    )
    request_record = db.scalar(
        select(DetectionRequestRecord)
        .where(DetectionRequestRecord.id == admission.request_id)
        .with_for_update()
    )
    current = _database_now(db)
    if (
        quota_row is None
        or request_record is None
        or request_record.status != "processing"
        or request_record.owner_token != admission.owner_token
        or request_record.usage_date != admission.usage_date
        or not _lease_is_active(request_record.lease_expires_at, current)
    ):
        raise DetectionReservationLostError

    used = int(quota_row.used or 0)
    if used + admission.reserved_chars > limit:
        raise QuotaExceededError(limit=limit, used_today=used, remaining=max(limit - used, 0))
    quota_row.limit = limit
    return request_record, quota_row


def consume_reserved_quota(
    quota_row: QuotaUsage,
    *,
    chars: int,
    limit: int,
) -> QuotaConsumeResult:
    quota_row.used = int(quota_row.used or 0) + chars
    quota_row.limit = limit
    return QuotaConsumeResult(
        limit=limit,
        used_today=quota_row.used,
        remaining=max(limit - quota_row.used, 0),
    )


def complete_detection_request(request_record: DetectionRequestRecord, *, detection_id: int) -> None:
    request_record.status = "completed"
    request_record.detection_id = detection_id


def fail_detection_request(request_record: DetectionRequestRecord) -> None:
    request_record.status = "failed"
    request_record.owner_token = str(uuid4())


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
