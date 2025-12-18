from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status

from app.db.deps import CurrentUserDep, SessionDep
from app.schemas import DetectionListResponse, DetectionRequest, DetectionResponse, ErrorResponse
from app.services.detection_service import DetectionService

router = APIRouter(tags=["detections"])


@router.post(
    "/detect",
    response_model=DetectionResponse,
    summary="对文本进行检测（stub 版）",
    responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def detect(payload: DetectionRequest, db: SessionDep, current_user: CurrentUserDep) -> DetectionResponse:
    if not payload.text.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Text cannot be empty")

    service = DetectionService(db)
    detection = service.create_detection(user_id=current_user.id, text=payload.text, options=payload.options)

    return DetectionResponse(detection_id=detection.id, label=detection.result_label, score=detection.score)


@router.get(
    "/detections",
    response_model=DetectionListResponse,
    summary="分页查询检测记录",
    responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def list_detections(
    db: SessionDep,
    current_user: CurrentUserDep,
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(10, ge=1, le=100, description="每页数量"),
    from_time: datetime | None = Query(None, alias="from", description="开始时间，ISO8601"),
    to_time: datetime | None = Query(None, alias="to", description="结束时间，ISO8601"),
) -> DetectionListResponse:
    if from_time and to_time and from_time > to_time:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="from must be <= to")

    service = DetectionService(db)
    records, total = service.list_detections(
        user_id=current_user.id, page=page, page_size=page_size, from_time=from_time, to_time=to_time
    )

    return DetectionListResponse(total=total, page=page, page_size=page_size, items=records)
