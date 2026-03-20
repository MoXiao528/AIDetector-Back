"""Detection related Pydantic models."""

from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field

from app.schemas.base import SchemaBase
from app.schemas.history import Analysis


class DetectionRequest(SchemaBase):
    text: str = Field(
        ...,
        min_length=1,
        json_schema_extra={"example": "Sample text to classify."},
    )
    functions: list[str] = Field(
        default_factory=list,
        description="Detection feature flags.",
        json_schema_extra={"example": ["scan", "polish"]},
    )
    options: dict[str, Any] | None = Field(
        default=None,
        description="Optional detection parameters.",
        json_schema_extra={"example": {"language": "en"}},
    )


class DetectionResponse(SchemaBase):
    detection_id: int = Field(..., json_schema_extra={"example": 1})
    label: str = Field(..., json_schema_extra={"example": "human"})
    score: float = Field(
        ...,
        ge=0,
        le=1,
        description="Normalized detection score in the range [0, 1].",
        json_schema_extra={"example": 0.68},
    )
    model_name: str | None = Field(
        default=None,
        description="Underlying model name returned by RepreGuard.",
        json_schema_extra={"example": "Qwen/Qwen2.5-7B"},
    )
    raw_score: float | None = Field(
        default=None,
        description="Raw score returned by the downstream model.",
        json_schema_extra={"example": 2.93},
    )
    threshold: float | None = Field(
        default=None,
        description="Threshold returned by the downstream model.",
        json_schema_extra={"example": 2.4924452377944597},
    )
    currentCredits: int = Field(
        ...,
        description="Remaining credits after deduction.",
        json_schema_extra={"example": 9990},
    )
    history_id: int | None = Field(
        default=None,
        description="Associated history record ID.",
        json_schema_extra={"example": 123},
    )
    input_text: str = Field(
        ...,
        description="Original input text.",
        json_schema_extra={"example": "Sample text to classify."},
    )
    result: Analysis | None = Field(
        default=None,
        description="Detailed analysis payload aligned with history records.",
    )


class DetectionItem(SchemaBase):
    id: int = Field(..., json_schema_extra={"example": 1})
    label: str = Field(..., json_schema_extra={"example": "ai"})
    score: float = Field(..., ge=0, le=1, json_schema_extra={"example": 0.42})
    input_text: str = Field(..., json_schema_extra={"example": "Short text"})
    created_at: datetime = Field(..., json_schema_extra={"example": "2024-01-01T00:00:00Z"})
    meta_json: dict | None = Field(
        default=None,
        description="Additional metadata.",
        json_schema_extra={
            "example": {
                "length": 120,
                "repre_guard": {
                    "raw_score": 2.93,
                    "threshold": 2.49,
                    "label": "AI",
                    "model_name": "Qwen/Qwen2.5-7B",
                },
            }
        },
    )

    @classmethod
    def from_orm_detection(cls, detection):
        return cls(
            id=detection.id,
            label=detection.result_label,
            score=detection.score,
            input_text=detection.input_text,
            created_at=detection.created_at,
            meta_json=detection.meta_json,
        )

    model_config = ConfigDict(from_attributes=True)


class DetectionListResponse(SchemaBase):
    total: int = Field(..., json_schema_extra={"example": 23})
    page: int = Field(..., json_schema_extra={"example": 1})
    page_size: int = Field(..., json_schema_extra={"example": 10})
    items: list[DetectionItem]
