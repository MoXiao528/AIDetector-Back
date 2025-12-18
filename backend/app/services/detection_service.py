"""Stub detection logic used by the API.

TODO: 替换为真实的模型推理调用：
1. 在此处加载/注入大模型或外部检测服务的客户端。
2. 使用模型输出的 label / score 替换下方的启发式计算。
3. 根据需要扩展 meta_json（例如模型版本、耗时、调用 ID 等），并注意避免记录敏感字段。
"""

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

    def create_detection(self, user_id: int, text: str, options: Mapping[str, Any] | None = None) -> Detection:
        detection_result = self._heuristic_score(text)

        merged_meta: dict[str, Any] = {"options": dict(options) if options else None, **detection_result.meta}

        detection = Detection(
            user_id=user_id,
            input_text=text,
            result_label=detection_result.label,
            score=detection_result.score,
            meta_json=merged_meta,
        )

        self.db.add(detection)
        self.db.commit()
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
