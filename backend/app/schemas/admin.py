"""Admin-related Pydantic models."""

from datetime import datetime
from enum import Enum

from pydantic import Field

from app.core.roles import UserRole
from app.schemas.base import SchemaBase
from app.schemas.history import Analysis


class AdminOverviewPreset(str, Enum):
    TODAY = "today"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class AdminOverviewGranularity(str, Enum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class AdminStatusResponse(SchemaBase):
    message: str = Field(..., json_schema_extra={"example": "admin ok"})


class AdminUserProfile(SchemaBase):
    first_name: str | None = Field(default=None, json_schema_extra={"example": "Jane"})
    surname: str | None = Field(default=None, json_schema_extra={"example": "Doe"})
    organization: str | None = Field(default=None, json_schema_extra={"example": "Acme Inc."})
    job_role: str | None = Field(default=None, json_schema_extra={"example": "Teacher"})
    industry: str | None = Field(default=None, json_schema_extra={"example": "Education"})


class AdminRecentUserItem(SchemaBase):
    id: int = Field(..., json_schema_extra={"example": 1})
    email: str = Field(..., json_schema_extra={"example": "user@example.com"})
    name: str = Field(..., json_schema_extra={"example": "alex"})
    system_role: UserRole = Field(..., json_schema_extra={"example": UserRole.INDIVIDUAL})
    is_active: bool = Field(..., json_schema_extra={"example": True})
    created_at: datetime = Field(..., json_schema_extra={"example": "2026-03-19T10:00:00Z"})


class AdminRecentDetectionItem(SchemaBase):
    id: int = Field(..., json_schema_extra={"example": 1})
    user_id: int | None = Field(default=None, json_schema_extra={"example": 1})
    user_email: str | None = Field(default=None, json_schema_extra={"example": "user@example.com"})
    user_name: str | None = Field(default=None, json_schema_extra={"example": "alex"})
    actor_type: str = Field(..., json_schema_extra={"example": "user"})
    actor_id: str = Field(..., json_schema_extra={"example": "1"})
    label: str = Field(..., json_schema_extra={"example": "ai"})
    score: float = Field(..., ge=0, le=1, json_schema_extra={"example": 0.82})
    chars_used: int = Field(..., json_schema_extra={"example": 1200})
    created_at: datetime = Field(..., json_schema_extra={"example": "2026-03-19T10:00:00Z"})


class AdminOverviewPeriod(SchemaBase):
    preset: AdminOverviewPreset = Field(..., json_schema_extra={"example": AdminOverviewPreset.WEEK})
    granularity: AdminOverviewGranularity = Field(..., json_schema_extra={"example": AdminOverviewGranularity.DAY})
    start_at: datetime = Field(..., json_schema_extra={"example": "2026-03-16T00:00:00Z"})
    end_at: datetime = Field(..., json_schema_extra={"example": "2026-03-23T00:00:00Z"})


class AdminOverviewSummary(SchemaBase):
    total_users: int = Field(..., json_schema_extra={"example": 120})
    active_users: int = Field(..., json_schema_extra={"example": 110})
    new_users: int = Field(..., json_schema_extra={"example": 35})
    detections: int = Field(..., json_schema_extra={"example": 365})
    chars_used: int = Field(..., json_schema_extra={"example": 420000})


class AdminOverviewSeriesItem(SchemaBase):
    bucket_start: datetime = Field(..., json_schema_extra={"example": "2026-03-19T00:00:00Z"})
    bucket_label: str = Field(..., json_schema_extra={"example": "03-19"})
    new_users: int = Field(..., json_schema_extra={"example": 5})
    detections: int = Field(..., json_schema_extra={"example": 48})
    chars_used: int = Field(..., json_schema_extra={"example": 60000})


class AdminOverviewResponse(SchemaBase):
    period: AdminOverviewPeriod
    summary: AdminOverviewSummary
    series: list[AdminOverviewSeriesItem]
    recent_users: list[AdminRecentUserItem]
    recent_detections: list[AdminRecentDetectionItem]


class AdminUserListItem(SchemaBase):
    id: int = Field(..., json_schema_extra={"example": 1})
    email: str = Field(..., json_schema_extra={"example": "user@example.com"})
    name: str = Field(..., json_schema_extra={"example": "alex"})
    system_role: UserRole = Field(..., json_schema_extra={"example": UserRole.INDIVIDUAL})
    plan_tier: str = Field(..., json_schema_extra={"example": "personal-free"})
    is_active: bool = Field(..., json_schema_extra={"example": True})
    credits_total: int = Field(..., json_schema_extra={"example": 30000})
    credits_used: int = Field(..., json_schema_extra={"example": 5000})
    credits_remaining: int = Field(..., json_schema_extra={"example": 25000})
    created_at: datetime = Field(..., json_schema_extra={"example": "2026-03-19T10:00:00Z"})


class AdminUserListResponse(SchemaBase):
    items: list[AdminUserListItem]
    page: int = Field(..., json_schema_extra={"example": 1})
    page_size: int = Field(..., json_schema_extra={"example": 20})
    total: int = Field(..., json_schema_extra={"example": 120})


class AdminDetectionMini(SchemaBase):
    id: int = Field(..., json_schema_extra={"example": 1})
    label: str = Field(..., json_schema_extra={"example": "ai"})
    score: float = Field(..., ge=0, le=1, json_schema_extra={"example": 0.85})
    chars_used: int = Field(..., json_schema_extra={"example": 1200})
    created_at: datetime = Field(..., json_schema_extra={"example": "2026-03-19T10:00:00Z"})


class AdminUserDetailResponse(AdminUserListItem):
    profile: AdminUserProfile | None = None
    recent_detections: list[AdminDetectionMini] = Field(default_factory=list)


class AdminUserUpdateRequest(SchemaBase):
    system_role: UserRole | None = None
    plan_tier: str | None = None
    is_active: bool | None = None


class AdminUserCreditsAdjustRequest(SchemaBase):
    delta: int = Field(..., json_schema_extra={"example": 5000})
    reason: str = Field(..., min_length=1, max_length=255, json_schema_extra={"example": "Manual grant"})


class AdminUserCreditsAdjustResponse(SchemaBase):
    user_id: int = Field(..., json_schema_extra={"example": 1})
    credits_total: int = Field(..., json_schema_extra={"example": 35000})
    credits_used: int = Field(..., json_schema_extra={"example": 5000})
    credits_remaining: int = Field(..., json_schema_extra={"example": 30000})


class AdminDetectionListItem(SchemaBase):
    id: int = Field(..., json_schema_extra={"example": 1})
    user_id: int | None = Field(default=None, json_schema_extra={"example": 1})
    user_email: str | None = Field(default=None, json_schema_extra={"example": "user@example.com"})
    user_name: str | None = Field(default=None, json_schema_extra={"example": "alex"})
    actor_type: str = Field(..., json_schema_extra={"example": "user"})
    actor_id: str = Field(..., json_schema_extra={"example": "1"})
    label: str = Field(..., json_schema_extra={"example": "ai"})
    score: float = Field(..., ge=0, le=1, json_schema_extra={"example": 0.84})
    chars_used: int = Field(..., json_schema_extra={"example": 1200})
    functions_used: list[str] = Field(default_factory=list, json_schema_extra={"example": ["scan"]})
    created_at: datetime = Field(..., json_schema_extra={"example": "2026-03-19T10:00:00Z"})


class AdminDetectionListResponse(SchemaBase):
    items: list[AdminDetectionListItem]
    page: int = Field(..., json_schema_extra={"example": 1})
    page_size: int = Field(..., json_schema_extra={"example": 20})
    total: int = Field(..., json_schema_extra={"example": 320})


class AdminDetectionDetailResponse(AdminDetectionListItem):
    title: str | None = Field(default=None, json_schema_extra={"example": "scan record"})
    input_text: str = Field(..., json_schema_extra={"example": "sample text"})
    editor_html: str | None = Field(default=None, json_schema_extra={"example": "<p>sample text</p>"})
    meta_json: dict | None = Field(default=None, json_schema_extra={"example": {"repre_guard": {"model_name": "Qwen"}}})
    analysis: Analysis | None = None
