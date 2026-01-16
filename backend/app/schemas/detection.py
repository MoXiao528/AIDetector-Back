"""Detection related Pydantic models."""

from datetime import datetime
from typing import Any, Optional

from pydantic import Field, ConfigDict

from app.schemas.base import SchemaBase


class DetectionRequest(SchemaBase):
    text: str = Field(
        ...,
        min_length=1,
        max_length=20000,
        example="Sample text to classify.",
    )
    functions: list[str] = Field(
        default_factory=list,
        description="检测时启用的功能列表。",
        example=["scan", "polish"],
    )
    options: dict[str, Any] | None = Field(
        default=None,
        description="可选的检测参数（例如语言、模型选择等），后端可用来路由到不同检测模型。",
        example={"language": "en"},
    )


class DetectionResponse(SchemaBase):
    """
    单次检测的返回结果：
    - detection_id：后端内部检测记录的主键 ID
    - label：最终归一化后的标签（例如 'human' / 'ai'）
    - score：0-1 范围的置信度（面向前端展示）
    - model_name / raw_score / threshold：底层 RepreGuard 微服务返回的原始信息
    """

    detection_id: int = Field(..., example=1)
    label: str = Field(..., example="human")
    score: float = Field(
        ...,
        ge=0,
        le=1,
        example=0.68,
        description="归一化后的分值（0-1），用于前端展示置信度。",
    )
    model_name: Optional[str] = Field(
        default=None,
        example="Qwen/Qwen2.5-7B",
        description="底层使用的检测模型名称（例如 RepreGuard 使用的基座模型）。",
    )
    raw_score: Optional[float] = Field(
        default=None,
        example=2.93,
        description="底层检测模型的原始分数（例如 RepreGuard 的 RepreScore），未归一化。",
    )
    threshold: Optional[float] = Field(
        default=None,
        example=2.4924452377944597,
        description="当前模型使用的分类阈值（例如 RepreGuard 的判别阈值）。",
    )
    currentCredits: int = Field(..., example=9990, description="Remaining credits after deduction")


class DetectionItem(SchemaBase):
    id: int = Field(..., example=1)
    label: str = Field(..., example="ai")
    score: float = Field(..., ge=0, le=1, example=0.42)
    input_text: str = Field(..., example="Short text")
    created_at: datetime = Field(..., example="2024-01-01T00:00:00Z")
    meta_json: dict | None = Field(
        default=None,
        example={
            "length": 120,
            "repre_guard": {
                "raw_score": 2.93,
                "threshold": 2.49,
                "label": "AI",
                "model_name": "Qwen/Qwen2.5-7B",
            },
        },
        description="额外元信息，例如文本长度、RepreGuard 原始分数等。",
    )

    @classmethod
    def from_orm_detection(cls, d):
        return cls(
            id=d.id,
            label=d.result_label,
            score=d.score,
            input_text=d.input_text,
            created_at=d.created_at,
            meta_json=d.meta_json,
        )

    model_config = ConfigDict(from_attributes=True)


class DetectionListResponse(SchemaBase):
    total: int = Field(..., example=23)
    page: int = Field(..., example=1)
    page_size: int = Field(..., example=10)
    items: list[DetectionItem]
