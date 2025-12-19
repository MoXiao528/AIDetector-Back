from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.team import TeamMemberRole


class TeamCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, example="My Awesome Team")


class TeamResponse(BaseModel):
    id: int = Field(..., example=1)
    name: str = Field(..., example="My Awesome Team")
    created_by_id: int | None = Field(None, example=1)
    created_at: datetime = Field(..., example="2024-01-01T00:00:00Z")

    class Config:
        from_attributes = True


class TeamMemberCreateRequest(BaseModel):
    user_id: int = Field(..., example=2)
    role: TeamMemberRole = Field(..., example=TeamMemberRole.MEMBER)


class TeamMemberResponse(BaseModel):
    id: int = Field(..., example=1)
    team_id: int = Field(..., example=1)
    user_id: int = Field(..., example=2)
    role: TeamMemberRole = Field(..., example=TeamMemberRole.MEMBER)
    joined_at: datetime = Field(..., example="2024-01-01T00:00:00Z")

    class Config:
        from_attributes = True


class TeamStatsItem(BaseModel):
    date: date = Field(..., example="2024-01-01")
    detections: int = Field(..., example=5)


class TeamStatsResponse(BaseModel):
    team_id: int = Field(..., example=1)
    items: list[TeamStatsItem]
