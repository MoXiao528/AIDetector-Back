from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.base import SchemaBase
from app.schemas.history import Analysis


class ReportPdfRequest(SchemaBase):
    report_type: Literal["scan", "history"] = Field(
        default="scan",
        description="Report source type.",
        json_schema_extra={"example": "scan"},
    )
    locale: str = Field(
        default="zh-CN",
        description="Report locale.",
        json_schema_extra={"example": "zh-CN"},
    )
    history_id: int = Field(..., ge=1, description="Associated history record ID.", json_schema_extra={"example": 12})


class ReportPdfContent(SchemaBase):
    report_type: Literal["scan", "history"] = Field(default="scan")
    generated_at: datetime | None = Field(default=None)
    locale: str = Field(default="zh-CN")
    history_id: int | None = Field(default=None)
    detection_id: int | None = Field(default=None)
    functions: list[str] = Field(
        default_factory=lambda: ["scan"],
        description="Functions used for this report.",
        json_schema_extra={"example": ["scan"]},
    )
    input_text: str = Field(
        ...,
        min_length=1,
        max_length=50000,
        description="Original input text.",
    )
    analysis: Analysis = Field(
        ...,
        description="Detection analysis payload.",
    )
