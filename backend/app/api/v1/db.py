import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas import DBPingResponse, ErrorResponse

router = APIRouter(tags=["database"])
logger = logging.getLogger(__name__)


@router.get(
    "/db/ping",
    response_model=DBPingResponse,
    summary="数据库连通性检查",
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def ping_database(db: Session = Depends(get_db)) -> DBPingResponse:
    """执行简单查询验证数据库连接。"""
    try:
        result = db.execute(text("SELECT 1")).scalar()
    except SQLAlchemyError as exc:  # pragma: no cover - 防御性兜底
        logger.error("DB ping failed", exc_info=exc)
        raise HTTPException(status_code=503, detail="Database not reachable")

    if result != 1:
        logger.error("DB ping unexpected result", extra={"result": result})
        raise HTTPException(status_code=503, detail="Database not reachable")

    return DBPingResponse(status="ok")
