from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.core.roles import UserRole
from app.models.detection import Detection
from app.models.user import User

logger = logging.getLogger(__name__)


@dataclass
class DetectionWithUser:
    detection: Detection
    user: User | None


@dataclass
class OverviewPeriodWindow:
    preset: str
    granularity: str
    start: datetime
    end: datetime


@dataclass
class OverviewBucket:
    start: datetime
    end: datetime
    label: str


@dataclass
class AdminOverviewData:
    period: dict[str, str | datetime]
    summary: dict[str, int]
    series: list[dict[str, str | int | datetime]]
    recent_users: list[User]
    recent_detections: list[DetectionWithUser]


class AdminService:
    RECENT_USERS_LIMIT = 5
    RECENT_DETECTIONS_LIMIT = 10
    USER_DETAIL_RECENT_DETECTIONS_LIMIT = 5

    def __init__(self, db: Session):
        self.db = db

    def get_overview(self, *, preset: str = "week") -> AdminOverviewData:
        period = self._resolve_overview_period(preset)

        summary = {
            "total_users": self._count_users(),
            "active_users": self._count_users(is_active=True),
            "new_users": self._count_users(start=period.start, end=period.end),
            "detections": self._count_detections(start=period.start, end=period.end),
            "chars_used": self._sum_chars(start=period.start, end=period.end),
        }

        recent_users = list(
            self.db.scalars(select(User).order_by(User.created_at.desc()).limit(self.RECENT_USERS_LIMIT)).all()
        )
        recent_detections = self._list_recent_detections(limit=self.RECENT_DETECTIONS_LIMIT)

        return AdminOverviewData(
            period={
                "preset": period.preset,
                "granularity": period.granularity,
                "start_at": period.start,
                "end_at": period.end,
            },
            summary=summary,
            series=self._build_series(period=period),
            recent_users=recent_users,
            recent_detections=recent_detections,
        )

    def list_users(
        self,
        *,
        page: int,
        page_size: int,
        search: str | None = None,
        system_role: UserRole | None = None,
        is_active: bool | None = None,
        plan_tier: str | None = None,
        sort: str = "createdAt",
        order: str = "desc",
    ) -> tuple[list[User], int]:
        query = select(User)

        if search:
            pattern = f"%{search.strip().lower()}%"
            query = query.where(
                or_(
                    func.lower(User.email).like(pattern),
                    func.lower(User.name).like(pattern),
                )
            )

        if system_role is not None:
            query = query.where(User.role == system_role)
        if is_active is not None:
            query = query.where(User.is_active.is_(is_active))
        if plan_tier:
            query = query.where(User.plan_tier == plan_tier)

        total = self._count_from_query(query)
        order_by = self._resolve_user_sort(sort=sort, order=order)
        records = list(
            self.db.scalars(query.order_by(order_by).offset((page - 1) * page_size).limit(page_size)).all()
        )
        return records, total

    def get_user(self, user_id: int) -> User | None:
        return self.db.get(User, user_id)

    def get_user_recent_detections(self, user_id: int) -> list[Detection]:
        return list(
            self.db.scalars(
                select(Detection)
                .where(Detection.user_id == user_id)
                .order_by(Detection.created_at.desc())
                .limit(self.USER_DETAIL_RECENT_DETECTIONS_LIMIT)
            ).all()
        )

    def update_user(
        self,
        user_id: int,
        *,
        system_role: UserRole | None = None,
        plan_tier: str | None = None,
        is_active: bool | None = None,
    ) -> User | None:
        user = self.get_user(user_id)
        if user is None:
            return None

        if system_role is not None:
            user.role = system_role
        if plan_tier is not None:
            user.plan_tier = plan_tier
        if is_active is not None:
            user.is_active = is_active

        self.db.commit()
        self.db.refresh(user)
        return user

    def adjust_user_credits(self, user_id: int, *, delta: int, reason: str) -> User | None:
        user = self.get_user(user_id)
        if user is None:
            return None

        next_total = user.credits_total + delta
        if next_total < user.credits_used:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "INVALID_CREDITS_ADJUSTMENT",
                    "message": "Credits total cannot be lower than credits used",
                    "detail": {
                        "creditsTotal": user.credits_total,
                        "creditsUsed": user.credits_used,
                        "delta": delta,
                    },
                },
            )

        user.credits_total = next_total
        logger.info(
            "Adjusted user credits",
            extra={"user_id": user_id, "delta": delta, "reason": reason},
        )
        self.db.commit()
        self.db.refresh(user)
        return user

    def list_detections(
        self,
        *,
        page: int,
        page_size: int,
        search: str | None = None,
        user_id: int | None = None,
        actor_type: str | None = None,
        label: str | None = None,
        function_name: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        sort: str = "createdAt",
        order: str = "desc",
    ) -> tuple[list[DetectionWithUser], int]:
        query = select(Detection, User).outerjoin(User, Detection.user_id == User.id)

        if search:
            pattern = f"%{search.strip().lower()}%"
            query = query.where(
                or_(
                    func.lower(Detection.input_text).like(pattern),
                    func.lower(Detection.actor_id).like(pattern),
                    func.lower(func.coalesce(User.email, "")).like(pattern),
                    func.lower(func.coalesce(User.name, "")).like(pattern),
                )
            )
        if user_id is not None:
            query = query.where(Detection.user_id == user_id)
        if actor_type:
            query = query.where(Detection.actor_type == actor_type)
        if label:
            query = query.where(Detection.result_label == label)
        if function_name:
            query = query.where(cast(Detection.functions_used, String).like(f'%"{function_name}"%'))
        if date_from is not None:
            query = query.where(Detection.created_at >= date_from)
        if date_to is not None:
            query = query.where(Detection.created_at <= date_to)

        total = self._count_from_query(query)
        order_by = self._resolve_detection_sort(sort=sort, order=order)
        rows = self.db.execute(query.order_by(order_by).offset((page - 1) * page_size).limit(page_size)).all()
        return [DetectionWithUser(detection=row[0], user=row[1]) for row in rows], total

    def get_detection(self, detection_id: int) -> DetectionWithUser | None:
        row = self.db.execute(
            select(Detection, User).outerjoin(User, Detection.user_id == User.id).where(Detection.id == detection_id)
        ).first()
        if row is None:
            return None
        return DetectionWithUser(detection=row[0], user=row[1])

    def delete_detection(self, detection_id: int) -> bool:
        detection = self.db.get(Detection, detection_id)
        if detection is None:
            return False
        self.db.delete(detection)
        self.db.commit()
        return True

    def _build_series(self, *, period: OverviewPeriodWindow) -> list[dict[str, str | int | datetime]]:
        buckets = self._build_buckets(start=period.start, end=period.end, granularity=period.granularity)
        user_counts = {bucket.start: 0 for bucket in buckets}
        detection_counts = {bucket.start: 0 for bucket in buckets}
        chars_by_bucket = {bucket.start: 0 for bucket in buckets}

        user_rows = self.db.scalars(
            select(User.created_at).where(User.created_at >= period.start, User.created_at < period.end)
        ).all()
        detection_rows = self.db.execute(
            select(Detection.created_at, Detection.chars_used).where(
                Detection.created_at >= period.start,
                Detection.created_at < period.end,
            )
        ).all()

        for created_at in user_rows:
            bucket = self._find_bucket(self._ensure_utc(created_at), buckets)
            if bucket is not None:
                user_counts[bucket.start] += 1

        for created_at, chars_used in detection_rows:
            bucket = self._find_bucket(self._ensure_utc(created_at), buckets)
            if bucket is not None:
                detection_counts[bucket.start] += 1
                chars_by_bucket[bucket.start] += int(chars_used or 0)

        return [
            {
                "bucket_start": bucket.start,
                "bucket_label": bucket.label,
                "new_users": user_counts[bucket.start],
                "detections": detection_counts[bucket.start],
                "chars_used": chars_by_bucket[bucket.start],
            }
            for bucket in buckets
        ]

    def _build_buckets(self, *, start: datetime, end: datetime, granularity: str) -> list[OverviewBucket]:
        buckets: list[OverviewBucket] = []
        current = start

        while current < end:
            if granularity == "hour":
                next_boundary = current + timedelta(hours=1)
                label = current.strftime("%H:00")
            elif granularity == "week":
                next_boundary = min(current + timedelta(days=7), end)
                label_end = next_boundary - timedelta(days=1)
                label = f"{current.strftime('%m-%d')} - {label_end.strftime('%m-%d')}"
            elif granularity == "month":
                next_boundary = self._add_months(current, 1)
                label = current.strftime("%Y-%m")
            else:
                next_boundary = current + timedelta(days=1)
                label = current.strftime("%m-%d")

            buckets.append(OverviewBucket(start=current, end=next_boundary, label=label))
            current = next_boundary

        return buckets

    def _find_bucket(self, value: datetime, buckets: list[OverviewBucket]) -> OverviewBucket | None:
        for bucket in buckets:
            if bucket.start <= value < bucket.end:
                return bucket
        return None

    def _list_recent_detections(self, *, limit: int) -> list[DetectionWithUser]:
        rows = self.db.execute(
            select(Detection, User)
            .outerjoin(User, Detection.user_id == User.id)
            .order_by(Detection.created_at.desc())
            .limit(limit)
        ).all()
        return [DetectionWithUser(detection=row[0], user=row[1]) for row in rows]

    def _count_users(
        self,
        *,
        is_active: bool | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> int:
        stmt = select(func.count(User.id))
        if is_active is not None:
            stmt = stmt.where(User.is_active.is_(is_active))
        if start is not None:
            stmt = stmt.where(User.created_at >= start)
        if end is not None:
            stmt = stmt.where(User.created_at < end)
        return int(self.db.scalar(stmt) or 0)

    def _count_detections(self, *, start: datetime | None = None, end: datetime | None = None) -> int:
        stmt = select(func.count(Detection.id))
        if start is not None:
            stmt = stmt.where(Detection.created_at >= start)
        if end is not None:
            stmt = stmt.where(Detection.created_at < end)
        return int(self.db.scalar(stmt) or 0)

    def _sum_chars(self, *, start: datetime | None = None, end: datetime | None = None) -> int:
        stmt = select(func.coalesce(func.sum(Detection.chars_used), 0))
        if start is not None:
            stmt = stmt.where(Detection.created_at >= start)
        if end is not None:
            stmt = stmt.where(Detection.created_at < end)
        return int(self.db.scalar(stmt) or 0)

    def _count_from_query(self, query) -> int:
        return int(self.db.scalar(select(func.count()).select_from(query.subquery())) or 0)

    def _resolve_user_sort(self, *, sort: str, order: str):
        credits_remaining = User.credits_total - User.credits_used
        mapping = {
            "createdAt": User.created_at,
            "email": User.email,
            "creditsRemaining": credits_remaining,
        }
        column = mapping.get(sort, User.created_at)
        return column.asc() if order == "asc" else column.desc()

    def _resolve_detection_sort(self, *, sort: str, order: str):
        mapping = {
            "createdAt": Detection.created_at,
            "score": Detection.score,
            "charsUsed": Detection.chars_used,
        }
        column = mapping.get(sort, Detection.created_at)
        return column.asc() if order == "asc" else column.desc()

    def _resolve_overview_period(self, preset: str) -> OverviewPeriodWindow:
        today_start = self._day_start()

        if preset == "today":
            return OverviewPeriodWindow(
                preset="today",
                granularity="hour",
                start=today_start,
                end=today_start + timedelta(days=1),
            )
        if preset == "month":
            month_start = datetime(today_start.year, today_start.month, 1, tzinfo=timezone.utc)
            return OverviewPeriodWindow(
                preset="month",
                granularity="day",
                start=month_start,
                end=self._add_months(month_start, 1),
            )
        if preset == "quarter":
            quarter_month = ((today_start.month - 1) // 3) * 3 + 1
            quarter_start = datetime(today_start.year, quarter_month, 1, tzinfo=timezone.utc)
            return OverviewPeriodWindow(
                preset="quarter",
                granularity="week",
                start=quarter_start,
                end=self._add_months(quarter_start, 3),
            )
        if preset == "year":
            year_start = datetime(today_start.year, 1, 1, tzinfo=timezone.utc)
            return OverviewPeriodWindow(
                preset="year",
                granularity="month",
                start=year_start,
                end=datetime(today_start.year + 1, 1, 1, tzinfo=timezone.utc),
            )

        week_start = today_start - timedelta(days=today_start.weekday())
        return OverviewPeriodWindow(
            preset="week",
            granularity="day",
            start=week_start,
            end=week_start + timedelta(days=7),
        )

    def _day_start(self) -> datetime:
        now = datetime.now(timezone.utc)
        return datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

    def _add_months(self, value: datetime, months: int) -> datetime:
        month_index = value.month - 1 + months
        year = value.year + month_index // 12
        month = month_index % 12 + 1
        return datetime(year, month, 1, tzinfo=timezone.utc)

    def _ensure_utc(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
