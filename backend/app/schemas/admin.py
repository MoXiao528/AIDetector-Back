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
    message: str = Field(..., example="admin ok")


class AdminUserProfile(SchemaBase):
    first_name: str | None = Field(default=None, example="Jane")
    surname: str | None = Field(default=None, example="Doe")
    organization: str | None = Field(default=None, example="Acme Inc.")
    job_role: str | None = Field(default=None, example="Teacher")
    industry: str | None = Field(default=None, example="Education")


class AdminRecentUserItem(SchemaBase):
    id: int = Field(..., example=1)
    email: str = Field(..., example="user@example.com")
    name: str = Field(..., example="alex")
    system_role: UserRole = Field(..., example=UserRole.INDIVIDUAL)
    is_active: bool = Field(..., example=True)
    created_at: datetime = Field(..., example="2026-03-19T10:00:00Z")


class AdminRecentDetectionItem(SchemaBase):
    id: int = Field(..., example=1)
    user_id: int | None = Field(default=None, example=1)
    user_email: str | None = Field(default=None, example="user@example.com")
    user_name: str | None = Field(default=None, example="alex")
    actor_type: str = Field(..., example="user")
    actor_id: str = Field(..., example="1")
    label: str = Field(..., example="ai")
    score: float = Field(..., ge=0, le=1, example=0.82)
    chars_used: int = Field(..., example=1200)
    created_at: datetime = Field(..., example="2026-03-19T10:00:00Z")


class AdminOverviewPeriod(SchemaBase):
    preset: AdminOverviewPreset = Field(..., example=AdminOverviewPreset.WEEK)
    granularity: AdminOverviewGranularity = Field(..., example=AdminOverviewGranularity.DAY)
    start_at: datetime = Field(..., example="2026-03-16T00:00:00Z")
    end_at: datetime = Field(..., example="2026-03-23T00:00:00Z")


class AdminOverviewSummary(SchemaBase):
    total_users: int = Field(..., example=120)
    active_users: int = Field(..., example=110)
    new_users: int = Field(..., example=35)
    detections: int = Field(..., example=365)
    chars_used: int = Field(..., example=420000)


class AdminOverviewSeriesItem(SchemaBase):
    bucket_start: datetime = Field(..., example="2026-03-19T00:00:00Z")
    bucket_label: str = Field(..., example="03-19")
    new_users: int = Field(..., example=5)
    detections: int = Field(..., example=48)
    chars_used: int = Field(..., example=60000)


class AdminOverviewResponse(SchemaBase):
    period: AdminOverviewPeriod
    summary: AdminOverviewSummary
    series: list[AdminOverviewSeriesItem]
    recent_users: list[AdminRecentUserItem]
    recent_detections: list[AdminRecentDetectionItem]


class AdminUserListItem(SchemaBase):
    id: int = Field(..., example=1)
    email: str = Field(..., example="user@example.com")
    name: str = Field(..., example="alex")
    system_role: UserRole = Field(..., example=UserRole.INDIVIDUAL)
    plan_tier: str = Field(..., example="personal-free")
    is_active: bool = Field(..., example=True)
    credits_total: int = Field(..., example=30000)
    credits_used: int = Field(..., example=5000)
    credits_remaining: int = Field(..., example=25000)
    created_at: datetime = Field(..., example="2026-03-19T10:00:00Z")


class AdminUserListResponse(SchemaBase):
    items: list[AdminUserListItem]
    page: int = Field(..., example=1)
    page_size: int = Field(..., example=20)
    total: int = Field(..., example=120)


class AdminDetectionMini(SchemaBase):
    id: int = Field(..., example=1)
    label: str = Field(..., example="ai")
    score: float = Field(..., ge=0, le=1, example=0.85)
    chars_used: int = Field(..., example=1200)
    created_at: datetime = Field(..., example="2026-03-19T10:00:00Z")


class AdminUserDetailResponse(AdminUserListItem):
    profile: AdminUserProfile | None = None
    recent_detections: list[AdminDetectionMini] = Field(default_factory=list)


class AdminUserUpdateRequest(SchemaBase):
    system_role: UserRole | None = None
    plan_tier: str | None = None
    is_active: bool | None = None


class AdminUserCreditsAdjustRequest(SchemaBase):
    delta: int = Field(..., example=5000)
    reason: str = Field(..., min_length=1, max_length=255, example="Manual grant")


class AdminUserCreditsAdjustResponse(SchemaBase):
    user_id: int = Field(..., example=1)
    credits_total: int = Field(..., example=35000)
    credits_used: int = Field(..., example=5000)
    credits_remaining: int = Field(..., example=30000)


class AdminDetectionListItem(SchemaBase):
    id: int = Field(..., example=1)
    user_id: int | None = Field(default=None, example=1)
    user_email: str | None = Field(default=None, example="user@example.com")
    user_name: str | None = Field(default=None, example="alex")
    actor_type: str = Field(..., example="user")
    actor_id: str = Field(..., example="1")
    label: str = Field(..., example="ai")
    score: float = Field(..., ge=0, le=1, example=0.84)
    chars_used: int = Field(..., example=1200)
    functions_used: list[str] = Field(default_factory=list, example=["scan"])
    created_at: datetime = Field(..., example="2026-03-19T10:00:00Z")


class AdminDetectionListResponse(SchemaBase):
    items: list[AdminDetectionListItem]
    page: int = Field(..., example=1)
    page_size: int = Field(..., example=20)
    total: int = Field(..., example=320)


class AdminDetectionDetailResponse(AdminDetectionListItem):
    title: str | None = Field(default=None, example="scan record")
    input_text: str = Field(..., example="sample text")
    editor_html: str | None = Field(default=None, example="<p>sample text</p>")
    meta_json: dict | None = Field(default=None, example={"repre_guard": {"model_name": "Qwen"}})
    analysis: Analysis | None = None
