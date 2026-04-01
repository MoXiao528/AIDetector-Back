from fastapi import APIRouter
from sqlalchemy import text

from app.db.deps import SessionDep
from app.schemas import ErrorResponse, HealthResponse

router = APIRouter(prefix="/db", tags=["database"])


@router.get(
    "/ping",
    response_model=HealthResponse,
    summary="数据库连接检查",
    responses={404: {"model": ErrorResponse}},
)
def ping_database(db: SessionDep) -> HealthResponse:
    db.execute(text("SELECT 1"))
    return HealthResponse(status="ok")

