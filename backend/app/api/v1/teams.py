from datetime import datetime

from fastapi import APIRouter, HTTPException, status

from app.db.deps import ActiveMemberDep, SessionDep
from app.schemas import (
    ErrorResponse,
    TeamCreateRequest,
    TeamMemberCreateRequest,
    TeamMemberResponse,
    TeamResponse,
    TeamStatsItem,
    TeamStatsResponse,
)
from app.services.team_service import TeamService

router = APIRouter(prefix="/teams", tags=["teams"])


@router.post(
    "",
    response_model=TeamResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建团队（创建者将成为 OWNER）",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def create_team(
    payload: TeamCreateRequest,
    db: SessionDep,
    current_user: ActiveMemberDep,
) -> TeamResponse:
    service = TeamService(db)
    team = service.create_team(name=payload.name, creator_id=current_user.id)
    return team


@router.post(
    "/{team_id}/members",
    response_model=TeamMemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="向团队添加成员（仅 OWNER/ADMIN 可操作）",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def add_team_member(
    team_id: int,
    payload: TeamMemberCreateRequest,
    db: SessionDep,
    current_user: ActiveMemberDep,
) -> TeamMemberResponse:
    service = TeamService(db)
    member = service.add_member(
        team_id=team_id,
        operator_id=current_user.id,
        user_id=payload.user_id,
        role=payload.role,
    )
    return member


@router.get(
    "/{team_id}/stats",
    response_model=TeamStatsResponse,
    summary="按天聚合团队检测次数（仅团队成员可访问）",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def get_team_stats(
    team_id: int,
    db: SessionDep,
    current_user: ActiveMemberDep,
    start: datetime | None = None,
    end: datetime | None = None,
) -> TeamStatsResponse:
    if start and end and start > end:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="start must be <= end")

    service = TeamService(db)
    rows = service.get_team_stats(team_id=team_id, user_id=current_user.id, start=start, end=end)
    items = [TeamStatsItem(date=row.day.date(), detections=row.count) for row in rows]
    return TeamStatsResponse(team_id=team_id, items=items)
