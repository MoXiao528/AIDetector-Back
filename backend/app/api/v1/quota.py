from fastapi import APIRouter

from app.db.deps import CurrentActorDep, SessionDep
from app.schemas import ErrorResponse, QuotaResponse
from app.services.quota_service import get_quota_limit, get_today_bounds, get_used_today

router = APIRouter(tags=["quota"])


@router.get(
    "/quota",
    response_model=QuotaResponse,
    summary="查询今日字符额度",
    responses={401: {"model": ErrorResponse}},
)
async def get_quota(
    db: SessionDep,
    current_actor: CurrentActorDep,
) -> QuotaResponse:
    day_start, day_end = get_today_bounds()
    used_today = get_used_today(
        db,
        actor_type=current_actor.actor_type,
        actor_id=current_actor.actor_id,
        start_time=day_start,
        end_time=day_end,
    )
    limit = get_quota_limit(current_actor.actor_type)
    remaining = max(limit - used_today, 0)

    return QuotaResponse(
        actor_type=current_actor.actor_type,
        limit=limit,
        used_today=used_today,
        remaining=remaining,
    )
