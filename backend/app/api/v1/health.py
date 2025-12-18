from fastapi import APIRouter

from app.schemas import ErrorResponse, HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="健康检查",
    responses={404: {"model": ErrorResponse}},
)
async def read_health() -> HealthResponse:
    return HealthResponse(status="ok")
