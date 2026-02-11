"""History API endpoints."""

from fastapi import APIRouter, HTTPException, Query, status

from app.db.deps import ActiveMemberDep, SessionDep
from app.schemas.history import (
    Analysis,
    BatchDeleteRequest,
    BatchDeleteResponse,
    ClearAllResponse,
    HistoryListResponse,
    HistoryRecordCreate,
    HistoryRecordResponse,
    HistoryRecordUpdate,
)
from app.services.history_service import HistoryService

router = APIRouter(prefix="/history", tags=["history"])


def _detection_to_history_response(detection) -> HistoryRecordResponse:
    """将 Detection 模型转换为 HistoryRecordResponse"""
    # 从 meta_json 中提取 analysis 数据
    analysis_data = None
    if detection.meta_json and "analysis" in detection.meta_json:
        try:
            analysis_data = Analysis(**detection.meta_json["analysis"])
        except Exception:
            # 如果解析失败，返回 None
            analysis_data = None

    return HistoryRecordResponse(
        id=detection.id,
        user_id=detection.user_id,
        title=detection.title,
        created_at=detection.created_at,
        functions=detection.functions_used or [],
        input_text=detection.input_text,
        editor_html=detection.editor_html,
        analysis=analysis_data,
    )


@router.get(
    "",
    response_model=HistoryListResponse,
    summary="获取历史记录列表",
)
async def list_histories(
    db: SessionDep,
    current_user: ActiveMemberDep,
    page: int = Query(1, ge=1, description="页码，默认 1"),
    per_page: int = Query(20, ge=1, le=100, description="每页数量，默认 20，最大 100"),
    sort: str = Query("created_at", description="排序字段，默认 created_at"),
    order: str = Query("desc", description="排序方向，asc 或 desc，默认 desc"),
) -> HistoryListResponse:
    """获取当前用户的历史记录列表（分页）"""
    service = HistoryService(db)
    records, total, total_pages = service.list_histories(
        user_id=current_user.id,
        page=page,
        per_page=per_page,
        sort=sort,
        order=order,
    )

    items = [_detection_to_history_response(r) for r in records]

    return HistoryListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )


@router.get(
    "/{history_id}",
    response_model=HistoryRecordResponse,
    summary="获取单条历史记录",
)
async def get_history(
    history_id: int,
    db: SessionDep,
    current_user: ActiveMemberDep,
) -> HistoryRecordResponse:
    """获取指定的历史记录"""
    service = HistoryService(db)
    detection = service.get_history(user_id=current_user.id, history_id=history_id)

    if not detection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "HISTORY_NOT_FOUND", "message": "历史记录不存在"},
        )

    return _detection_to_history_response(detection)


@router.post(
    "",
    response_model=HistoryRecordResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建历史记录",
)
async def create_history(
    payload: HistoryRecordCreate,
    db: SessionDep,
    current_user: ActiveMemberDep,
) -> HistoryRecordResponse:
    """创建新的历史记录"""
    # 验证输入文本长度
    if len(payload.input_text) > 50000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "INVALID_HISTORY_DATA", "message": "输入文本超过 50,000 字符限制"},
        )

    service = HistoryService(db)
    
    # 将 Analysis 转换为 dict 存储
    analysis_dict = None
    if payload.analysis:
        analysis_dict = payload.analysis.model_dump()

    detection = service.create_history(
        user_id=current_user.id,
        title=payload.title,
        functions=payload.functions,
        input_text=payload.input_text,
        editor_html=payload.editor_html,
        analysis=analysis_dict,
    )

    return _detection_to_history_response(detection)


@router.patch(
    "/{history_id}",
    response_model=HistoryRecordResponse,
    summary="更新历史记录",
)
async def update_history(
    history_id: int,
    payload: HistoryRecordUpdate,
    db: SessionDep,
    current_user: ActiveMemberDep,
) -> HistoryRecordResponse:
    """更新历史记录（仅允许更新 title）"""
    service = HistoryService(db)
    detection = service.update_history(
        user_id=current_user.id,
        history_id=history_id,
        title=payload.title,
    )

    if not detection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "HISTORY_NOT_FOUND", "message": "历史记录不存在"},
        )

    return _detection_to_history_response(detection)


@router.delete(
    "/{history_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除历史记录",
)
async def delete_history(
    history_id: int,
    db: SessionDep,
    current_user: ActiveMemberDep,
) -> None:
    """删除指定的历史记录"""
    service = HistoryService(db)
    success = service.delete_history(user_id=current_user.id, history_id=history_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "HISTORY_NOT_FOUND", "message": "历史记录不存在"},
        )


@router.post(
    "/batch-delete",
    response_model=BatchDeleteResponse,
    summary="批量删除历史记录",
)
async def batch_delete_histories(
    payload: BatchDeleteRequest,
    db: SessionDep,
    current_user: ActiveMemberDep,
) -> BatchDeleteResponse:
    """批量删除历史记录"""
    service = HistoryService(db)
    deleted_count, failed_ids = service.batch_delete_histories(
        user_id=current_user.id,
        ids=payload.ids,
    )

    return BatchDeleteResponse(
        deleted_count=deleted_count,
        failed_ids=failed_ids,
    )


@router.delete(
    "",
    response_model=ClearAllResponse,
    summary="清空所有历史记录",
)
async def clear_all_histories(
    db: SessionDep,
    current_user: ActiveMemberDep,
) -> ClearAllResponse:
    """清空当前用户的所有历史记录"""
    service = HistoryService(db)
    deleted_count = service.clear_all_histories(user_id=current_user.id)

    return ClearAllResponse(deleted_count=deleted_count)
