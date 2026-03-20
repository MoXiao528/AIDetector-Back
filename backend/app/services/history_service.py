"""History service for managing history records based on Detection model."""

from __future__ import annotations

from math import ceil
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.detection import Detection


class HistoryService:
    """History record service backed by the Detection model."""

    MAX_HISTORY_RECORDS = 100

    def __init__(self, db: Session):
        self.db = db

    def create_history(
        self,
        user_id: int,
        title: str,
        functions: list[str],
        input_text: str,
        editor_html: str,
        analysis: dict[str, Any] | None = None,
    ) -> Detection:
        if not input_text or not input_text.strip():
            raise ValueError("input_text cannot be empty")

        self._enforce_limit(user_id)

        meta_json: dict[str, Any] = {}
        if analysis:
            meta_json["analysis"] = analysis

        detection = Detection(
            user_id=user_id,
            actor_type="user",
            actor_id=str(user_id),
            chars_used=len(input_text),
            title=title,
            input_text=input_text,
            editor_html=editor_html,
            functions_used=functions,
            result_label="human",
            score=0.0,
            meta_json=meta_json,
        )

        self.db.add(detection)
        self.db.commit()
        self.db.refresh(detection)
        return detection

    def claim_guest_histories(self, user_id: int, guest_id: str) -> int:
        if not guest_id:
            return 0

        stmt = (
            select(Detection)
            .where(
                Detection.actor_type == "guest",
                Detection.actor_id == guest_id,
                Detection.user_id.is_(None),
            )
            .order_by(Detection.created_at.asc(), Detection.id.asc())
        )
        records = list(self.db.scalars(stmt).all())
        if not records:
            return 0

        claimed_count = 0
        for record in records:
            if not self._is_displayable_history(record):
                continue
            record.user_id = user_id
            claimed_count += 1

        self.db.commit()
        return claimed_count

    def get_history(self, user_id: int, history_id: int) -> Detection | None:
        stmt = select(Detection).where(
            Detection.id == history_id,
            Detection.user_id == user_id,
        )
        return self.db.scalar(stmt)

    def list_histories(
        self,
        user_id: int,
        page: int = 1,
        per_page: int = 20,
        sort: str = "created_at",
        order: str = "desc",
    ) -> tuple[list[Detection], int, int]:
        per_page = min(per_page, 100)

        query = select(Detection).where(Detection.user_id == user_id)

        if sort == "created_at":
            order_by = Detection.created_at.desc() if order == "desc" else Detection.created_at.asc()
        else:
            order_by = Detection.created_at.desc()

        ordered_records = list(self.db.scalars(query.order_by(order_by)).all())
        valid_records = [record for record in ordered_records if self._is_displayable_history(record)]

        total = len(valid_records)
        total_pages = ceil(total / per_page) if per_page > 0 else 0
        offset = (page - 1) * per_page
        records = valid_records[offset : offset + per_page]
        return records, total, total_pages

    def update_history(
        self,
        user_id: int,
        history_id: int,
        title: str,
    ) -> Detection | None:
        detection = self.get_history(user_id, history_id)
        if not detection:
            return None

        detection.title = title
        self.db.commit()
        self.db.refresh(detection)
        return detection

    def delete_history(self, user_id: int, history_id: int) -> bool:
        detection = self.get_history(user_id, history_id)
        if not detection:
            return False

        self.db.delete(detection)
        self.db.commit()
        return True

    def batch_delete_histories(
        self,
        user_id: int,
        ids: list[int],
    ) -> tuple[int, list[int]]:
        deleted_count = 0
        failed_ids = []

        for history_id in ids:
            success = self.delete_history(user_id, history_id)
            if success:
                deleted_count += 1
            else:
                failed_ids.append(history_id)

        return deleted_count, failed_ids

    def clear_all_histories(self, user_id: int) -> int:
        stmt = delete(Detection).where(Detection.user_id == user_id)
        result = self.db.execute(stmt)
        self.db.commit()
        return result.rowcount or 0

    def _enforce_limit(self, user_id: int) -> None:
        count_stmt = select(func.count()).select_from(Detection).where(Detection.user_id == user_id)
        total = self.db.scalar(count_stmt) or 0

        if total >= self.MAX_HISTORY_RECORDS:
            to_delete = total - self.MAX_HISTORY_RECORDS + 1
            oldest_stmt = (
                select(Detection.id)
                .where(Detection.user_id == user_id)
                .order_by(Detection.created_at.asc())
                .limit(to_delete)
            )
            oldest_ids = list(self.db.scalars(oldest_stmt).all())

            if oldest_ids:
                delete_stmt = delete(Detection).where(Detection.id.in_(oldest_ids))
                self.db.execute(delete_stmt)
                self.db.commit()

    @staticmethod
    def _is_displayable_history(record: Detection) -> bool:
        if not record.input_text or not record.input_text.strip():
            return False

        analysis = (record.meta_json or {}).get("analysis")
        if not isinstance(analysis, dict):
            return False

        if not analysis.get("summary") and not analysis.get("sentences"):
            return False

        return True
