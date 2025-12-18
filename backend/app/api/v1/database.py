import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.deps import SessionDep
from app.schemas import DatabasePingResponse, ErrorResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/db", tags=["database"])


@router.get(
    "/ping",
    response_model=DatabasePingResponse,
    summary="数据库连接检查",
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def ping_database(db: SessionDep) -> DatabasePingResponse:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.error("Database ping failed", exc_info=exc)
        raise HTTPException(status_code=503, detail="Database unavailable")
    return DatabasePingResponse(status="ok")
