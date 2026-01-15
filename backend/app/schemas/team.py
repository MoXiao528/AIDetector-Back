from datetime import date, datetime

from pydantic import Field, ConfigDict

from app.schemas.base import SchemaBase

from app.models.team import TeamMemberRole


class TeamCreateRequest(SchemaBase):
    name: str = Field(..., min_length=1, max_length=255, example="My Awesome Team")


class TeamResponse(SchemaBase):
    id: int = Field(..., example=1)
    name: str = Field(..., example="My Awesome Team")
    created_by_id: int | None = Field(None, example=1)
    created_at: datetime = Field(..., example="2024-01-01T00:00:00Z")

    model_config = ConfigDict(from_attributes=True)


class TeamMemberCreateRequest(SchemaBase):
    user_id: int = Field(..., example=2)
    role: TeamMemberRole = Field(..., example=TeamMemberRole.MEMBER)


class TeamMemberResponse(SchemaBase):
    id: int = Field(..., example=1)
    team_id: int = Field(..., example=1)
    user_id: int = Field(..., example=2)
    role: TeamMemberRole = Field(..., example=TeamMemberRole.MEMBER)
    joined_at: datetime = Field(..., example="2024-01-01T00:00:00Z")

    model_config = ConfigDict(from_attributes=True)


class TeamStatsItem(SchemaBase):
    day: date = Field(..., alias="date", example="2024-01-01")
    detections: int = Field(..., example=5)

    model_config = ConfigDict(populate_by_name=True)


class TeamStatsResponse(SchemaBase):
    team_id: int = Field(..., example=1)
    items: list[TeamStatsItem]
