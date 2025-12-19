"""Detection related Pydantic models."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DetectionRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=20000, example="Sample text to classify.")
    options: dict[str, Any] | None = Field(
        default=None,
        description="可选的检测参数（例如温度、模型名称等），当前 stub 忽略。",
        example={"language": "en"},
    )


class DetectionResponse(BaseModel):
    detection_id: int = Field(..., example=1)
    label: str = Field(..., example="human")
    score: float = Field(..., ge=0, le=1, example=0.68)


class DetectionItem(BaseModel):
    id: int = Field(..., example=1)
    label: str = Field(..., example="ai")
    score: float = Field(..., ge=0, le=1, example=0.42)
    input_text: str = Field(..., example="Short text")
    created_at: datetime = Field(..., example="2024-01-01T00:00:00Z")
    meta_json: dict | None = Field(default=None, example={"length": 120, "repetition_score": 0.12})

    @classmethod
    def from_orm_detection(cls, d):
        return cls(
            id=d.id,
            label=d.result_label,  # 👈 显式映射
            score=d.score,
            input_text=d.input_text,
            created_at=d.created_at,
            meta_json=d.meta_json,
        )

    class Config:
        from_attributes = True


class DetectionListResponse(BaseModel):
    total: int = Field(..., example=23)
    page: int = Field(..., example=1)
    page_size: int = Field(..., example=10)
    items: list[DetectionItem]
