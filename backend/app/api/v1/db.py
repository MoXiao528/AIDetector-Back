from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas import DatabasePingResponse, ErrorResponse

router = APIRouter(prefix="/db", tags=["database"])


@router.get(
    "/ping",
    response_model=DatabasePingResponse,
    summary="数据库连通性检查",
    responses={
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def ping_database(db: Annotated[Session, Depends(get_db)]) -> DatabasePingResponse:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:  # pragma: no cover - 运行时数据库异常
        raise HTTPException(status_code=503, detail="Database connectivity issue") from exc

    return DatabasePingResponse(status="ok")
