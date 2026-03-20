from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.deps import SessionDep
from app.schemas import ErrorResponse, HealthResponse, ReadinessResponse
from app.services.repre_guard_client import RepreGuardError, repre_guard_client

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness check",
    responses={404: {"model": ErrorResponse}},
)
async def read_health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness check",
    responses={503: {"model": ErrorResponse}},
)
async def read_readiness(db: SessionDep) -> ReadinessResponse:
    component_status = {
        "database": "pending",
        "detect_service": "pending",
    }

    try:
        db.execute(text("SELECT 1"))
        component_status["database"] = "ok"
    except SQLAlchemyError as exc:
        component_status["database"] = "error"
        component_status["detect_service"] = "skipped"
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "READINESS_CHECK_FAILED",
                "message": "Database is unavailable",
                "detail": {
                    "status": "error",
                    **component_status,
                },
            },
        ) from exc

    try:
        await repre_guard_client.health()
        component_status["detect_service"] = "ok"
    except RepreGuardError as exc:
        component_status["detect_service"] = "error"
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "READINESS_CHECK_FAILED",
                "message": "Detect service is unavailable",
                "detail": {
                    "status": "error",
                    **component_status,
                },
            },
        ) from exc

    return ReadinessResponse(status="ok", **component_status)
