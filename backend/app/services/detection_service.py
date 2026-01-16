from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.detection import Detection


@dataclass
class DetectionResult:
    label: str
    score: float
    meta: dict[str, Any]


class DetectionService:
    def __init__(self, db: Session):
        self.db = db

    def _heuristic_score(self, text: str) -> DetectionResult:
        """简单启发式：综合文本长度与重复率生成分数。

        - 长度越长越倾向 human（避免把空文本判为高可信）。
        - 重复率越高越倾向 ai（模拟机械输出）。
        """

        stripped = text.strip()
        length = len(stripped)
        length_factor = min(length / 800, 1.0)

        # 重复率估算：不同字符占比越低，认为越像 AI
        unique_chars = len(set(stripped)) or 1
        repetition_ratio = 1 - min(unique_chars / max(length, 1), 1)

        raw_score = max(min(0.6 * length_factor + 0.4 * (1 - repetition_ratio), 1.0), 0.0)
        label = "human" if raw_score >= 0.5 else "ai"

        meta = {
            "length": length,
            "length_factor": round(length_factor, 3),
            "repetition_ratio": round(repetition_ratio, 3),
            "method": "heuristic_stub",
        }

        return DetectionResult(label=label, score=round(raw_score, 4), meta=meta)

    def _sanitize_options(self, options: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not options:
            return None

        redacted_keys = {"password", "token", "secret", "api_key", "key"}
        sanitized: dict[str, Any] = {}
        for key, value in options.items():
            normalized_key = key.lower().strip()
            sanitized[normalized_key] = "***" if normalized_key in redacted_keys else value
        return sanitized

    def create_detection(
        self,
        user_id: int,
        text: str,
        options: Mapping[str, Any] | None = None,
        score: float | None = None,
        label: str | None = None,
        functions_used: list[str] | None = None,
        commit: bool = True,
    ) -> Detection:
        # 如果外部没传，用启发式兜底
        if score is None or label is None:
            detection_result = self._heuristic_score(text)
            final_score = detection_result.score
            final_label = detection_result.label
            meta_extra = detection_result.meta
        else:
            # 真实模型结果
            final_score = score
            final_label = label
            meta_extra = {"method": "repre_guard_v1"}

        merged_meta: dict[str, Any] = {
            "options": self._sanitize_options(options),
            **meta_extra
        }

        detection = Detection(
            user_id=user_id,
            input_text=text,
            result_label=final_label,
            score=final_score,
            functions_used=functions_used,
            meta_json=merged_meta,
        )

        self.db.add(detection)
        if commit:
            self.db.commit()
            self.db.refresh(detection)
        else:
            self.db.flush()
            self.db.refresh(detection)
        return detection

    def list_detections(
        self,
        user_id: int,
        page: int,
        page_size: int,
        from_time: Any | None,
        to_time: Any | None,
    ) -> tuple[list[Detection], int]:
        query = select(Detection).where(Detection.user_id == user_id)

        if from_time:
            query = query.where(Detection.created_at >= from_time)
        if to_time:
            query = query.where(Detection.created_at <= to_time)

        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0

        paginated_query = query.order_by(Detection.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        records = self.db.scalars(paginated_query).all()
        return records, total
